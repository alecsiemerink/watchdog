from __future__ import annotations

import io
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image, ImageDraw


@dataclass(frozen=True, slots=True)
class PersonObservation:
    confidence: float
    # Apple Vision normalized coordinates: origin is at the lower-left.
    bounding_box: tuple[float, float, float, float]


class VisionPersonDetector:
    """Local person detector backed by Apple's Vision framework."""

    def __init__(self, *, upper_body_only: bool = True) -> None:
        try:
            import objc
            import Vision
            from Foundation import NSData
        except ImportError as error:
            raise RuntimeError(
                "Person detection requires pyobjc-framework-Vision on macOS."
            ) from error
        self._NSData = NSData
        self._Vision = Vision
        self._autorelease_pool = objc.autorelease_pool
        self.upper_body_only = upper_body_only

    def detect(self, jpeg: bytes) -> list[PersonObservation]:
        with self._autorelease_pool():
            data = self._NSData.dataWithBytes_length_(jpeg, len(jpeg))
            request = self._Vision.VNDetectHumanRectanglesRequest.alloc().init()
            request.setUpperBodyOnly_(self.upper_body_only)
            handler = self._Vision.VNImageRequestHandler.alloc().initWithData_options_(
                data, {}
            )
            succeeded, error = handler.performRequests_error_([request], None)
            if not succeeded:
                raise RuntimeError(f"Apple Vision person detection failed: {error}")

            observations: list[PersonObservation] = []
            for result in request.results() or []:
                box = result.boundingBox()
                observations.append(
                    PersonObservation(
                        confidence=float(result.confidence()),
                        bounding_box=(
                            float(box.origin.x),
                            float(box.origin.y),
                            float(box.size.width),
                            float(box.size.height),
                        ),
                    )
                )
        return observations


class PersonPresence:
    """Debounces detector results into detected/cleared state transitions."""

    def __init__(
        self,
        *,
        confidence_threshold: float,
        required_hits: int,
        clear_hits: int,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.required_hits = max(1, required_hits)
        self.clear_hits = max(1, clear_hits)
        self.present = False
        self._positive_hits = 0
        self._negative_hits = 0

    def update(self, observations: list[PersonObservation]) -> str | None:
        positive = any(
            observation.confidence >= self.confidence_threshold
            for observation in observations
        )
        if positive:
            self._positive_hits += 1
            self._negative_hits = 0
            if not self.present and self._positive_hits >= self.required_hits:
                self.present = True
                return "detected"
        else:
            self._positive_hits = 0
            self._negative_hits += 1
            if self.present and self._negative_hits >= self.clear_hits:
                self.present = False
                return "cleared"
        return None


DetectionCallback = Callable[[str, list[PersonObservation], bytes], None]
Log = Callable[[str], None]


class PersonDetectionWorker:
    """Runs Vision off the capture loop and always analyzes the newest frame."""

    def __init__(
        self,
        detector: VisionPersonDetector,
        presence: PersonPresence,
        callback: DetectionCallback,
        log: Log = print,
    ) -> None:
        self.detector = detector
        self.presence = presence
        self.callback = callback
        self.log = log
        self._frames: queue.Queue[bytes | None] = queue.Queue(maxsize=1)
        self._thread: threading.Thread | None = None
        self._failed = False

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="person-detection",
            daemon=True,
        )
        self._thread.start()

    def submit(self, jpeg: bytes) -> None:
        if self._failed:
            return
        try:
            self._frames.put_nowait(jpeg)
        except queue.Full:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frames.put_nowait(jpeg)
            except queue.Full:
                pass

    def stop(self) -> None:
        try:
            self._frames.put_nowait(None)
        except queue.Full:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
            self._frames.put_nowait(None)
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while True:
            jpeg = self._frames.get()
            if jpeg is None:
                return
            try:
                observations = self.detector.detect(jpeg)
                event = self.presence.update(observations)
                if event:
                    self.callback(event, observations, jpeg)
            except Exception as error:  # noqa: BLE001 - optional worker isolation.
                self._failed = True
                self.log(f"Person detection disabled after an error: {error}")
                return


def annotate_people(jpeg: bytes, observations: list[PersonObservation]) -> bytes:
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for observation in observations:
        x, y, box_width, box_height = observation.bounding_box
        horizontal = sorted((round(x * width), round((x + box_width) * width)))
        # Vision uses a lower-left origin; Pillow uses upper-left.
        vertical = sorted(
            (
                round((1 - y - box_height) * height),
                round((1 - y) * height),
            )
        )
        left = max(0, min(width - 1, horizontal[0]))
        right = max(0, min(width - 1, horizontal[1]))
        top = max(0, min(height - 1, vertical[0]))
        bottom = max(0, min(height - 1, vertical[1]))
        if right <= left or bottom <= top:
            continue
        draw.rectangle((left, top, right, bottom), outline="#70F59A", width=3)
        label = f"person {observation.confidence:.0%}"
        label_top = max(0, top - 18)
        label_right = min(width - 1, left + 108)
        draw.rectangle((left, label_top, label_right, top), fill="#102018")
        draw.text((min(width - 1, left + 4), label_top + 2), label, fill="#70F59A")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=82)
    return output.getvalue()

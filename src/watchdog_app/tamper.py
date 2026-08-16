from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ViewMetrics:
    brightness: float
    contrast: float
    changed_fraction: float
    reason: str | None


class CameraTamperDetector:
    """Detects sustained darkness, flat coverage, or a major view shift."""

    def __init__(
        self,
        *,
        baseline_frames: int = 10,
        pixel_delta: int = 35,
        changed_fraction: float = 0.75,
        dark_brightness: float = 18,
        flat_contrast: float = 7,
        required_hits: int = 16,
        recovery_hits: int = 8,
    ) -> None:
        self.baseline_frames = max(1, baseline_frames)
        self.pixel_delta = pixel_delta
        self.changed_fraction_threshold = changed_fraction
        self.dark_brightness = dark_brightness
        self.flat_contrast = flat_contrast
        self.required_hits = max(1, required_hits)
        self.recovery_hits = max(1, recovery_hits)
        self.obstructed = False
        self._baseline_sums: list[int] | None = None
        self._baseline_count = 0
        self._baseline: bytes | None = None
        self._suspicious_hits = 0
        self._healthy_hits = 0

    @staticmethod
    def _brightness_and_contrast(sample: bytes) -> tuple[float, float]:
        if not sample:
            return 0.0, 0.0
        brightness = sum(sample) / len(sample)
        variance = sum((value - brightness) ** 2 for value in sample) / len(sample)
        return brightness, math.sqrt(variance)

    def _learn_baseline(self, sample: bytes) -> None:
        if self._baseline_sums is None:
            self._baseline_sums = [0] * len(sample)
        if len(sample) != len(self._baseline_sums):
            return
        for index, value in enumerate(sample):
            self._baseline_sums[index] += value
        self._baseline_count += 1
        if self._baseline_count >= self.baseline_frames:
            self._baseline = bytes(
                round(total / self._baseline_count) for total in self._baseline_sums
            )
            self._baseline_sums = None

    def update(self, sample: bytes) -> tuple[str | None, ViewMetrics]:
        brightness, contrast = self._brightness_and_contrast(sample)
        if self._baseline is None:
            self._learn_baseline(sample)
            reason = None
            if brightness <= self.dark_brightness:
                reason = "dark"
            elif contrast <= self.flat_contrast:
                reason = "covered"
            metrics = ViewMetrics(brightness, contrast, 0.0, reason)
            return self._transition(reason is not None), metrics

        baseline_brightness, _ = self._brightness_and_contrast(self._baseline)
        current_centered = (value - brightness for value in sample)
        baseline_centered = (value - baseline_brightness for value in self._baseline)
        changed = sum(
            1
            for current, baseline in zip(current_centered, baseline_centered)
            if abs(current - baseline) >= self.pixel_delta
        )
        changed_fraction = changed / max(1, len(sample))

        reason: str | None = None
        if brightness <= self.dark_brightness:
            reason = "dark"
        elif contrast <= self.flat_contrast:
            reason = "covered"
        elif changed_fraction >= self.changed_fraction_threshold:
            reason = "moved or obstructed"

        metrics = ViewMetrics(brightness, contrast, changed_fraction, reason)
        return self._transition(reason is not None), metrics

    def _transition(self, suspicious: bool) -> str | None:
        if suspicious:
            self._suspicious_hits += 1
            self._healthy_hits = 0
            if not self.obstructed and self._suspicious_hits >= self.required_hits:
                self.obstructed = True
                return "obstructed"
        else:
            self._suspicious_hits = 0
            self._healthy_hits += 1
            if self.obstructed and self._healthy_hits >= self.recovery_hits:
                self.obstructed = False
                return "recovered"
        return None

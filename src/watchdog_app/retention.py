from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RetentionResult:
    removed: tuple[Path, ...]
    reclaimed_bytes: int
    total_bytes: int
    free_bytes: int
    critically_low: bool


def apply_retention(
    output_dir: Path,
    *,
    max_age_days: int,
    max_total_bytes: int,
    minimum_free_bytes: int,
    active_path: Path | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> RetentionResult:
    now = now if now is not None else time.time()
    active_resolved = active_path.resolve() if active_path else None
    media = sorted(
        (
            path
            for pattern in (
                "motion_*.mp4",
                "motion_*.jpg",
                "person_*.jpg",
                "tamper_*.jpg",
            )
            for path in output_dir.glob(pattern)
            if path.is_file()
        ),
        key=lambda path: path.stat().st_mtime,
    )
    candidates = [
        path
        for path in media
        if active_resolved is None or path.resolve() != active_resolved
    ]
    sizes = {path: path.stat().st_size for path in media}
    total_bytes = sum(sizes.values())
    cutoff = now - max_age_days * 86_400 if max_age_days > 0 else None
    removed: list[Path] = []
    reclaimed = 0

    def remove(path: Path) -> None:
        nonlocal total_bytes, reclaimed
        size = sizes[path]
        removed.append(path)
        reclaimed += size
        total_bytes -= size
        if not dry_run:
            path.unlink(missing_ok=True)

    for path in list(candidates):
        if cutoff is not None and path.stat().st_mtime < cutoff:
            remove(path)

    for path in candidates:
        if path in removed:
            continue
        free_bytes = shutil.disk_usage(output_dir).free + (reclaimed if dry_run else 0)
        over_total = max_total_bytes > 0 and total_bytes > max_total_bytes
        low_free = minimum_free_bytes > 0 and free_bytes < minimum_free_bytes
        if not over_total and not low_free:
            break
        remove(path)

    free_bytes = shutil.disk_usage(output_dir).free + (reclaimed if dry_run else 0)
    critically_low = minimum_free_bytes > 0 and free_bytes < minimum_free_bytes
    return RetentionResult(
        removed=tuple(removed),
        reclaimed_bytes=reclaimed,
        total_bytes=total_bytes,
        free_bytes=free_bytes,
        critically_low=critically_low,
    )

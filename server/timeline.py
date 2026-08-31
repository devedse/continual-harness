"""Helpers for exposing the active run's per-step screenshot timeline."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

_STEP_SCREENSHOT_RE = re.compile(r"^step_(\d{6})\.png$")
_CACHE_LOCK = threading.RLock()
_FRAME_CACHE: dict[Path, tuple[int, list[dict[str, Any]]]] = {}


def _copy_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep callers from mutating the process-wide metadata cache."""
    return [dict(frame) for frame in frames]


def discover_timeline_frames(run_dir: Path) -> list[dict[str, Any]]:
    """Return sorted frame metadata, rescanning only when the directory changes."""
    screenshots_dir = Path(run_dir) / "screenshots"
    if not screenshots_dir.is_dir():
        return []

    try:
        cache_key = screenshots_dir.resolve()
        directory_version = screenshots_dir.stat().st_mtime_ns
    except OSError:
        return []

    with _CACHE_LOCK:
        cached = _FRAME_CACHE.get(cache_key)
        if cached and cached[0] == directory_version:
            cached_frames = cached[1]
            # A retry atomically replaces the latest logical step. Most file
            # systems update the directory timestamp too, but this direct stat
            # keeps replacement detection reliable when they do not.
            if not cached_frames:
                return []
            latest = cached_frames[-1]
            latest_path = screenshots_dir / f"step_{latest['step']:06d}.png"
            try:
                latest_stat = latest_path.stat()
            except OSError:
                pass
            else:
                if (
                    latest_stat.st_mtime_ns == latest["version"]
                    and latest_stat.st_size == latest["size"]
                ):
                    return _copy_frames(cached_frames)

    frames: list[dict[str, Any]] = []
    for path in screenshots_dir.iterdir():
        match = _STEP_SCREENSHOT_RE.fullmatch(path.name)
        if not match or not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        step = int(match.group(1))
        frames.append(
            {
                "step": step,
                "version": stat.st_mtime_ns,
                "size": stat.st_size,
                "url": f"/api/timeline/frames/{step}?v={stat.st_mtime_ns}",
            }
        )

    frames.sort(key=lambda frame: frame["step"])
    with _CACHE_LOCK:
        _FRAME_CACHE[cache_key] = (directory_version, frames)
    return _copy_frames(frames)


def select_timeline_delta(
    frames: list[dict[str, Any]],
    after_step: int,
    known_last_version: int | None,
) -> list[dict[str, Any]]:
    """Return newly appended frames plus a replaced previous final frame."""
    delta = [frame for frame in frames if frame["step"] > after_step]
    previous_final = next(
        (frame for frame in frames if frame["step"] == after_step),
        None,
    )
    if previous_final and previous_final["version"] != known_last_version:
        delta.insert(0, previous_final)
    return _copy_frames(delta)


def resolve_timeline_frame(run_dir: Path, step: int) -> Path | None:
    """Resolve one exact numbered screenshot without accepting user paths."""
    if isinstance(step, bool) or not isinstance(step, int) or step < 0 or step > 999999:
        return None
    path = Path(run_dir) / "screenshots" / f"step_{step:06d}.png"
    return path if path.is_file() else None

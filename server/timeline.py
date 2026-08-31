"""Helpers for exposing the active run's per-step screenshot timeline."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

_STEP_SCREENSHOT_RE = re.compile(r"^step_(\d{6})\.png$")
_CACHE_LOCK = threading.RLock()
_FRAME_CACHE: dict[Path, tuple[int, list[dict[str, Any]]]] = {}
_STEP_CACHE: dict[Path, tuple[tuple[int, int], list[dict[str, Any]]]] = {}


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


def resolve_trajectory_file(run_dir: Path, run_id: str | None = None) -> Path | None:
    """Find the live trajectory first, then the finalized run-data copy."""
    candidates: list[Path] = []
    if run_id:
        candidates.append(Path(".pokeagent_cache") / str(run_id) / "trajectory_history.jsonl")
    candidates.append(Path(run_dir) / "trajectory_history.jsonl")
    return next((path for path in candidates if path.is_file()), None)


def _normalize_buttons(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        return []
    return [str(button).upper() for button in values if str(button).strip()]


def normalize_step_detail(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce a trajectory row to the safe, compact shape used by both UIs."""
    try:
        step = int(entry.get("step"))
    except (TypeError, ValueError):
        return None
    if step < 0:
        return None

    action = entry.get("action") if isinstance(entry.get("action"), dict) else {}
    raw_calls = action.get("tool_calls")
    if not isinstance(raw_calls, list):
        raw_calls = []
        legacy_name = action.get("tool") or action.get("name")
        if legacy_name:
            raw_calls.append({"name": legacy_name, "args": action})

    buttons: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            continue
        name = str(raw_call.get("name") or raw_call.get("tool") or "unknown")
        args = raw_call.get("args") if isinstance(raw_call.get("args"), dict) else {}
        call_buttons = _normalize_buttons(args.get("buttons"))
        if name == "press_buttons":
            buttons.extend(call_buttons)
        tool_calls.append({"name": name, "buttons": call_buttons})

    coords = entry.get("player_coords")
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        coords = None

    return {
        "step": step,
        "timestamp": str(entry.get("timestamp") or ""),
        "analysis": str(entry.get("reasoning") or "").strip(),
        "buttons": buttons,
        "tool_calls": tool_calls,
        "location": str(entry.get("location") or ""),
        "player_coords": list(coords[:2]) if coords else None,
        "objective_context": str(entry.get("objective_context") or ""),
    }


def discover_step_details(run_dir: Path, run_id: str | None = None) -> list[dict[str, Any]]:
    """Return the latest trajectory record for every step, sorted oldest first."""
    trajectory_file = resolve_trajectory_file(run_dir, run_id)
    if trajectory_file is None:
        return []
    try:
        cache_key = trajectory_file.resolve()
        stat = trajectory_file.stat()
        version = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return []

    with _CACHE_LOCK:
        cached = _STEP_CACHE.get(cache_key)
        if cached and cached[0] == version:
            return _copy_frames(cached[1])

    by_step: dict[int, dict[str, Any]] = {}
    try:
        with trajectory_file.open("r", encoding="utf-8") as trajectory_stream:
            for line in trajectory_stream:
                try:
                    raw_entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(raw_entry, dict):
                    continue
                detail = normalize_step_detail(raw_entry)
                if detail is not None:
                    # Retries can append the same logical step. The last row is
                    # authoritative, matching the screenshot replacement policy.
                    by_step[detail["step"]] = detail
    except OSError:
        return []

    details = [by_step[step] for step in sorted(by_step)]
    with _CACHE_LOCK:
        _STEP_CACHE[cache_key] = (version, details)
    return _copy_frames(details)


def select_recent_step_details(
    details: list[dict[str, Any]], limit: int = 25
) -> list[dict[str, Any]]:
    """Return a bounded chronological tail for initial live-view hydration."""
    safe_limit = max(1, min(int(limit), 100))
    return _copy_frames(details[-safe_limit:])

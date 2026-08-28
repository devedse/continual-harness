"""Helpers for resuming a run from its run-specific cache."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_run_id(run_id: str) -> str:
    """Validate a user-provided run id before using it as a directory name."""
    value = (run_id or "").strip()
    if not _RUN_ID_RE.fullmatch(value):
        raise ValueError(
            "run id must start with a letter or digit and contain only "
            "letters, digits, '.', '_' or '-'"
        )
    return value


def cache_directory_for_run(run_id: str, base_dir: Path | str = ".pokeagent_cache") -> Path:
    return Path(base_dir) / validate_run_id(run_id)


def find_latest_evolved_prompt(run_id: str, run_data_dir: Path | str = "run_data") -> Optional[Path]:
    """Return the newest evolved orchestrator prompt for a run, if one exists."""
    prompt_dir = Path(run_data_dir) / validate_run_id(run_id) / "prompt_evolution" / "meta_prompts"
    prompts = list(prompt_dir.glob("steps_*.md"))
    return max(prompts, key=lambda path: path.stat().st_mtime) if prompts else None


def write_agent_state(path: Path | str, state: Dict[str, Any]) -> None:
    """Atomically persist agent-local state alongside the emulator checkpoint."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.flush()
    temporary.replace(destination)


def load_agent_state(cache_dir: Path | str, history_limit: int = 20) -> Dict[str, Any]:
    """Load exact agent state, or reconstruct the useful subset from legacy files."""
    cache_path = Path(cache_dir)
    state_path = cache_path / "agent_state.json"
    if state_path.exists():
        with state_path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        state["source"] = "agent_state.json"
        state["conversation_history"] = list(state.get("conversation_history") or [])[-history_limit:]
        return state

    step = _load_checkpoint_step(cache_path / "checkpoint_llm.txt")
    trajectories = _load_recent_trajectories(cache_path / "trajectory_history.jsonl", history_limit)
    if trajectories:
        step = max(step or 0, max(int(item.get("step") or 0) for item in trajectories))

    history = _history_from_trajectories(trajectories)
    return {
        "version": 1,
        "step_count": step or 0,
        "conversation_history": history,
        "recent_function_results": [],
        "source": "legacy_checkpoints",
    }


def _load_checkpoint_step(path: Path) -> Optional[int]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle).get("agent_step_count")
        return int(value) if value is not None else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _load_recent_trajectories(path: Path, limit: int) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows[-limit:]


def _history_from_trajectories(trajectories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    history: List[Dict[str, Any]] = []
    for index, trajectory in enumerate(trajectories):
        pre_state = trajectory.get("pre_state") or {}
        start_coords = pre_state.get("player_coords")
        next_state = trajectories[index + 1].get("pre_state") if index + 1 < len(trajectories) else None
        end_coords = (next_state or {}).get("player_coords") or start_coords
        tool_calls = ((trajectory.get("action") or {}).get("tool_calls") or [])
        entry = {
            "step": int(trajectory.get("step") or 0),
            "llm_response": trajectory.get("reasoning") or "",
            "timestamp": trajectory.get("timestamp"),
            "tool_calls": tool_calls,
            "start_coords": start_coords,
            "end_coords": end_coords,
            "player_coords": end_coords,
        }
        if tool_calls:
            entry["action"] = tool_calls[-1].get("name", "unknown")
            entry["action_details"] = f"{entry['action']}(...)"
        history.append(entry)
    return history

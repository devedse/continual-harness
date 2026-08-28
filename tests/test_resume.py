import json
import os

import pytest

from utils.data_persistence.resume import (
    cache_directory_for_run,
    find_latest_evolved_prompt,
    load_agent_state,
    validate_run_id,
    write_agent_state,
)


def test_validate_run_id_rejects_paths():
    assert validate_run_id("pokemon-red_01.test") == "pokemon-red_01.test"
    for value in ("../escape", "nested/run", "", "has space"):
        with pytest.raises(ValueError):
            validate_run_id(value)


def test_cache_directory_for_run_is_scoped(tmp_path):
    assert cache_directory_for_run("my-run", tmp_path) == tmp_path / "my-run"


def test_write_and_load_exact_agent_state(tmp_path):
    state = {
        "version": 1,
        "step_count": 12,
        "conversation_history": [{"step": value} for value in range(25)],
        "recent_function_results": [["press_buttons", "{}", 1.0]],
    }
    write_agent_state(tmp_path / "agent_state.json", state)

    restored = load_agent_state(tmp_path, history_limit=20)

    assert restored["source"] == "agent_state.json"
    assert restored["step_count"] == 12
    assert restored["conversation_history"][0]["step"] == 5
    assert len(restored["conversation_history"]) == 20


def test_load_agent_state_reconstructs_legacy_checkpoints(tmp_path):
    (tmp_path / "checkpoint_llm.txt").write_text(
        json.dumps({"agent_step_count": 8}), encoding="utf-8"
    )
    trajectories = [
        {
            "step": 7,
            "timestamp": "now",
            "reasoning": "move right",
            "action": {"tool_calls": [{"name": "press_buttons", "args": {"buttons": ["RIGHT"]}}]},
            "pre_state": {"player_coords": [1, 2]},
        },
        {
            "step": 8,
            "timestamp": "later",
            "reasoning": "press A",
            "action": {"tool_calls": [{"name": "press_buttons", "args": {"buttons": ["A"]}}]},
            "pre_state": {"player_coords": [2, 2]},
        },
    ]
    (tmp_path / "trajectory_history.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in trajectories), encoding="utf-8"
    )

    restored = load_agent_state(tmp_path)

    assert restored["source"] == "legacy_checkpoints"
    assert restored["step_count"] == 8
    assert restored["conversation_history"][0]["start_coords"] == [1, 2]
    assert restored["conversation_history"][0]["end_coords"] == [2, 2]
    assert restored["conversation_history"][1]["tool_calls"][0]["name"] == "press_buttons"


def test_find_latest_evolved_prompt(tmp_path):
    prompt_dir = tmp_path / "run-1" / "prompt_evolution" / "meta_prompts"
    prompt_dir.mkdir(parents=True)
    older = prompt_dir / "steps_1_to_25.md"
    newer = prompt_dir / "steps_26_to_50.md"
    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    assert find_latest_evolved_prompt("run-1", tmp_path) == newer

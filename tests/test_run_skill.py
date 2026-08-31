import json
from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents.PokeAgent import PokeAgent


def _agent_for_skill():
    agent = PokeAgent.__new__(PokeAgent)
    agent.mcp_adapter = SimpleNamespace(call_tool=Mock(return_value={"success": True}))
    agent._wait_for_actions_complete = Mock()
    return agent


def _execute(agent, code, args=None):
    entry = SimpleNamespace(name="Test Skill", code=code)
    store = SimpleNamespace(get=Mock(return_value=entry))
    with patch("utils.stores.skills.get_skill_store", return_value=store):
        return json.loads(agent._execute_run_skill({
            "skill_id": "test_skill",
            "reasoning": "regression test",
            "args": {} if args is None else args,
        }))


def test_run_skill_accepts_positional_press_buttons_call():
    agent = _agent_for_skill()

    result = _execute(
        agent,
        "tools['press_buttons'](['A'], reasoning='advance'); result = {'success': True}",
    )

    assert result["success"] is True
    agent.mcp_adapter.call_tool.assert_called_once_with(
        "press_buttons", {"buttons": ["A"], "reasoning": "advance"}
    )
    agent._wait_for_actions_complete.assert_called_once_with()


def test_run_skill_allows_no_argument_skill():
    agent = _agent_for_skill()

    result = _execute(agent, "result = {'success': True, 'value': 42}")

    assert result["success"] is True
    assert result["result"]["value"] == 42


def test_run_skill_propagates_skill_reported_failure():
    agent = _agent_for_skill()

    result = _execute(
        agent,
        "result = {'success': False, 'error': 'destination unreachable'}",
    )

    assert result["success"] is False
    assert result["error"] == "destination unreachable"


def test_run_skill_returns_traceback_with_skill_name_and_line():
    agent = _agent_for_skill()

    result = _execute(agent, "value = 1\nraise ValueError('broken route')")

    assert result["success"] is False
    assert result["error_type"] == "ValueError"
    assert "<skill:test_skill>" in result["traceback"]
    assert "line 2" in result["traceback"]
    assert "broken route" in result["traceback"]


def test_action_history_only_replays_recent_tool_results():
    agent = PokeAgent.__new__(PokeAgent)
    agent.conversation_history = [
        {
            "step": step,
            "llm_response": f"thought {step}",
            "tool_calls": [
                {
                    "name": "run_skill",
                    "args": {"skill_id": f"skill_{step}"},
                    "result": f"result {step}",
                }
            ],
        }
        for step in range(1, 6)
    ]

    history = agent._format_action_history()

    assert "thought 1" in history
    assert "result 1" not in history
    assert "result 2" not in history
    assert "result 3" in history
    assert "result 5" in history


def _history_entry(step):
    return {
        "step": step,
        "llm_response": f"thought {step}",
        "start_coords": (step, 0),
        "end_coords": (step, 1),
        "tool_calls": [
            {
                "name": "press_buttons",
                "args": {"buttons": ["A"], "marker": step},
                "result": f"result {step}",
            }
        ],
    }


def _history_slots(history):
    slots = {}
    for block in history.split("\n#### HISTORY SLOT ")[1:]:
        slots[block[:2]] = block[3:]
    return slots


def test_action_history_fixed_slots_only_replace_the_expired_record():
    agent = PokeAgent.__new__(PokeAgent)
    agent.conversation_history = [_history_entry(step) for step in range(1, 21)]
    first = _history_slots(agent._format_action_history(include_recent_tool_results=False))

    agent.conversation_history = [_history_entry(step) for step in range(2, 22)]
    second = _history_slots(agent._format_action_history(include_recent_tool_results=False))

    assert first.keys() == second.keys()
    assert [slot for slot in first if first[slot] != second[slot]] == ["01"]
    assert "[1]" in first["01"]
    assert "[21]" in second["01"]


def test_history_split_preserves_every_previous_field():
    agent = PokeAgent.__new__(PokeAgent)
    agent.conversation_history = [_history_entry(step) for step in range(1, 21)]

    fixed_history = agent._format_action_history(include_recent_tool_results=False)
    recent_results = agent._format_recent_history_tool_results()

    for step in range(1, 21):
        assert f"[{step}]" in fixed_history
        assert f"thought {step}" in fixed_history
        assert f'"marker": {step}' in fixed_history
    for step in range(1, 18):
        assert f'result: "result {step}"' not in recent_results
    for step in range(18, 21):
        assert f'result: "result {step}"' in recent_results

    assert agent._format_action_history_chronology() == (
        "Oldest → newest: " + " → ".join(f"[{step}]" for step in range(1, 21))
    )


def test_state_context_split_is_lossless_and_moves_volatile_data_to_tail():
    state = """RECENT ACTION HISTORY:
  1. DOWN @ (1,1) → (1,2)

=== PLAYER INFO ===
Player Name: RED
Position: X=1, Y=2

=== LOCATION & MAP INFO ===
Current Location: ROUTE_1
Player Position: (1, 2)

=== MAP (FULL) ===
Location: ROUTE_1
Dimensions: 20x36

ASCII Map:
#I.#

Map Data (JSON):
{"objects": []}

=== GAME STATE ===
Game State: overworld
MOVEMENT PREVIEW:
  DOWN: WALKABLE"""

    split = PokeAgent._split_state_context(state)

    assert sum(len(value) for value in split.values()) == len(state)
    original_lines = Counter(state.splitlines(keepends=True))
    split_lines = Counter(
        line
        for value in split.values()
        for line in value.splitlines(keepends=True)
    )
    assert split_lines == original_lines
    assert "RECENT ACTION HISTORY" in split["recent_actions"]
    assert "Current Location: ROUTE_1" in split["slow"]
    assert "Dimensions: 20x36" in split["slow"]
    assert "#I.#" in split["live"]
    assert "Position: X=1, Y=2" in split["live"]
    assert "Player Position: (1, 2)" in split["live"]
    assert "Game State: overworld" in split["live"]


def test_emerald_map_layout_keeps_only_location_and_dimensions_in_slow_state():
    state = """=== LOCATION & MAP INFO ===
Current Location: LITTLEROOT TOWN
Player Position: (5, 7)

=== PORYMAP MAP LAYOUT ===
Location: LittlerootTown
Dimensions: 20x20

ASCII Map:
.....
..P..

Map Data (JSON):
{"objects": []}"""

    split = PokeAgent._split_state_context(state)

    assert sum(len(value) for value in split.values()) == len(state)
    assert "Current Location: LITTLEROOT TOWN" in split["slow"]
    assert "Dimensions: 20x20" in split["slow"]
    assert "Player Position: (5, 7)" in split["live"]
    assert "..P.." in split["live"]

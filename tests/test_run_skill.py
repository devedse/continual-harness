import json
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

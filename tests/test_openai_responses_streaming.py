from types import SimpleNamespace

from agents.tools.registry import TOOL_REGISTRY
from utils.agent_infrastructure.vlm_backends import OpenAIBackend


class _FakeResponseStream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def __iter__(self):
        return iter(self.events)

    def close(self):
        self.closed = True


def test_call_responses_streams_and_returns_assembled_final_response():
    expected = object()
    response_stream = _FakeResponseStream(
        [
            SimpleNamespace(type="response.created"),
            SimpleNamespace(type="response.completed", response=expected),
        ]
    )
    calls = []

    backend = OpenAIBackend.__new__(OpenAIBackend)
    backend.model_name = "test-model"
    backend.client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **kwargs: calls.append(kwargs) or response_stream,
        )
    )

    result = backend._call_responses(
        "system instructions",
        {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        tools=[{"type": "function", "name": "press_buttons"}],
    )

    assert result is expected
    assert response_stream.closed
    assert calls == [
        {
            "model": "test-model",
            "instructions": "system instructions",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                }
            ],
            "tools": [{"type": "function", "name": "press_buttons"}],
            "tool_choice": "auto",
            "stream": True,
        }
    ]


def test_call_responses_streams_text_input_without_tools():
    expected = object()
    response_stream = _FakeResponseStream(
        [SimpleNamespace(type="response.completed", response=expected)]
    )
    calls = []

    backend = OpenAIBackend.__new__(OpenAIBackend)
    backend.model_name = "test-model"
    backend.client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **kwargs: calls.append(kwargs) or response_stream,
        )
    )

    assert backend._call_responses(None, "hello") is expected
    assert calls == [{"model": "test-model", "input": "hello", "stream": True}]
    assert response_stream.closed


def test_openai_tool_schema_preserves_array_items_that_are_objects():
    process_memory = next(tool for tool in TOOL_REGISTRY if tool["name"] == "process_memory")
    backend = OpenAIBackend.__new__(OpenAIBackend)
    backend.tools = [process_memory]

    converted = backend._convert_tools_to_openai_format()[0]
    entries = converted["parameters"]["properties"]["entries"]

    assert entries["type"] == "array"
    assert entries["items"]["type"] == "object"
    assert entries["items"]["properties"]["importance"]["type"] == "integer"
    assert entries["items"]["properties"]["content"]["type"] == "string"


def test_openai_process_skill_schema_requires_action_specific_fields():
    process_skill = next(tool for tool in TOOL_REGISTRY if tool["name"] == "process_skill")
    backend = OpenAIBackend.__new__(OpenAIBackend)
    backend.tools = [process_skill]

    parameters = backend._convert_tools_to_openai_format()[0]["parameters"]

    add_rule, existing_entry_rule = parameters["allOf"]
    add_item = add_rule["then"]["properties"]["entries"]["items"]
    existing_item = existing_entry_rule["then"]["properties"]["entries"]["items"]
    assert add_item["required"] == ["name", "description"]
    assert add_item["properties"]["description"]["minLength"] == 1
    assert existing_item["required"] == ["id"]

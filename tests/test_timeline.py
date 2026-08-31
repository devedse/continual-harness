import base64
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.app import _interaction_precedes_live_cursor, app
from server.timeline import (
    discover_step_details,
    discover_timeline_frames,
    normalize_step_detail,
    resolve_timeline_frame,
    select_recent_step_details,
    select_timeline_delta,
)
from utils.data_persistence.run_data_manager import RunDataManager

_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
    "x8AAwMCAO7Z0ioAAAAASUVORK5CYII="
)


def test_discover_timeline_frames_only_lists_numbered_pngs(tmp_path):
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    (screenshots / "step_000010.png").write_bytes(b"ten")
    (screenshots / "step_000002.png").write_bytes(b"two")
    (screenshots / "step_000003.png.tmp").write_bytes(b"temporary")
    (screenshots / "notes.png").write_bytes(b"not a frame")

    frames = discover_timeline_frames(tmp_path)

    assert [frame["step"] for frame in frames] == [2, 10]
    assert all(frame["url"].startswith(f"/api/timeline/frames/{frame['step']}?v=") for frame in frames)
    assert [frame["size"] for frame in frames] == [3, 3]


def test_resolve_timeline_frame_cannot_escape_screenshot_directory(tmp_path):
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    expected = screenshots / "step_000007.png"
    expected.write_bytes(b"frame")

    assert resolve_timeline_frame(tmp_path, 7) == expected
    assert resolve_timeline_frame(tmp_path, -1) is None
    assert resolve_timeline_frame(tmp_path, 1_000_000) is None
    assert resolve_timeline_frame(tmp_path, True) is None


def test_discover_timeline_frames_handles_missing_directory(tmp_path):
    assert discover_timeline_frames(Path(tmp_path)) == []


def test_timeline_delta_returns_new_and_replaced_final_frames():
    frames = [
        {"step": 10, "version": 100, "size": 1, "url": "/10"},
        {"step": 11, "version": 200, "size": 1, "url": "/11"},
        {"step": 12, "version": 300, "size": 1, "url": "/12"},
    ]

    assert [frame["step"] for frame in select_timeline_delta(frames, 11, 200)] == [12]
    assert [frame["step"] for frame in select_timeline_delta(frames, 11, 199)] == [11, 12]


def test_timeline_api_and_frame_endpoint_use_active_run(tmp_path):
    manager = RunDataManager(run_id="9", base_dir=str(tmp_path))
    manager.save_step_screenshot(12, _PNG_BASE64)
    client = TestClient(app)

    with patch(
        "utils.data_persistence.run_data_manager.get_run_data_manager",
        return_value=manager,
    ):
        index_response = client.get("/api/timeline")
        frame_response = client.get("/api/timeline/frames/12")
        missing_response = client.get("/api/timeline/frames/11")

    assert index_response.status_code == 200
    assert index_response.json()["run_id"] == "9"
    assert index_response.json()["last_step"] == 12
    assert index_response.json()["incremental"] is False
    assert frame_response.status_code == 200
    assert frame_response.content == base64.b64decode(_PNG_BASE64)
    assert frame_response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert missing_response.status_code == 404


def test_timeline_api_returns_only_incremental_frames(tmp_path):
    manager = RunDataManager(run_id="9", base_dir=str(tmp_path))
    manager.save_step_screenshot(12, _PNG_BASE64)
    manager.save_step_screenshot(13, _PNG_BASE64)
    client = TestClient(app)

    with patch(
        "utils.data_persistence.run_data_manager.get_run_data_manager",
        return_value=manager,
    ):
        response = client.get(
            "/api/timeline",
            params={
                "after_step": 12,
                "known_last_version": 0,
                "known_count": 1,
                "client_run_id": "9",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["incremental"] is True
    assert payload["reset"] is False
    assert payload["count"] == 2
    assert [frame["step"] for frame in payload["frames"]] == [12, 13]


def test_timeline_and_stream_pages_link_to_each_other():
    client = TestClient(app)

    timeline_response = client.get("/timeline")
    stream_response = client.get("/stream")

    assert timeline_response.status_code == 200
    assert 'href="/stream"' in timeline_response.text
    assert 'href="/timeline"' in stream_response.text


def test_timeline_keeps_thumbnail_scrolling_inside_the_filmstrip():
    page = Path("server/timeline.html").read_text(encoding="utf-8")

    assert "scrollIntoView" not in page
    assert "dom.filmstrip.scrollTo" in page
    assert "overflow-x: clip" in page
    assert ".lower-panel" in page and "overflow: hidden" in page


def test_timeline_refresh_preserves_clicks_made_while_fetch_is_in_flight():
    page = Path("server/timeline.html").read_text(encoding="utf-8")

    fetch_position = page.index("await fetch(indexUrl")
    selection_position = page.index("const selectedAtApply = currentFrame()")
    assert selection_position > fetch_position
    assert "nearestIndex(selectedAtApply.step)" in page
    assert "if (refreshInFlight) return" in page
    assert "finally {\n                refreshInFlight = false;" in page


def test_timeline_polling_uses_incremental_index_parameters():
    page = Path("server/timeline.html").read_text(encoding="utf-8")

    assert "indexUrl.searchParams.set('after_step'" in page
    assert "indexUrl.searchParams.set('known_last_version'" in page
    assert "indexUrl.searchParams.set('known_count'" in page
    assert "indexUrl.searchParams.set('client_run_id'" in page
    assert "if (data.incremental && !data.reset)" in page


def test_unchanged_timeline_poll_does_not_recenter_the_filmstrip():
    page = Path("server/timeline.html").read_text(encoding="utf-8")

    unchanged_guard = page.index("if (!initialLoad && !changed)")
    selection_restore = page.index("if (initialLoad)", unchanged_guard)
    guarded_block = page[unchanged_guard:selection_restore]

    assert "updateControls()" in guarded_block
    assert "loadStepDetail(selected.step)" in guarded_block
    assert "return;" in guarded_block
    assert "renderFilmstrip()" not in guarded_block


def test_step_detail_normalizes_reasoning_buttons_and_tools():
    detail = normalize_step_detail(
        {
            "step": 42,
            "timestamp": "2026-08-31T10:00:00",
            "reasoning": "Inspect the menu, then confirm.",
            "location": "Route1",
            "player_coords": [3, 7],
            "action": {
                "tool_calls": [
                    {"name": "press_buttons", "args": {"buttons": ["down", "A"]}},
                    {"name": "process_memory", "args": {"action": "read"}},
                ]
            },
        }
    )

    assert detail["analysis"] == "Inspect the menu, then confirm."
    assert detail["buttons"] == ["DOWN", "A"]
    assert detail["tool_calls"] == [
        {"name": "press_buttons", "buttons": ["DOWN", "A"]},
        {"name": "process_memory", "buttons": []},
    ]
    assert detail["player_coords"] == [3, 7]


def test_step_history_uses_latest_retry_and_returns_bounded_tail(tmp_path):
    trajectory = tmp_path / "trajectory_history.jsonl"
    rows = [
        {"step": 1, "reasoning": "first", "action": {}},
        {"step": 2, "reasoning": "old attempt", "action": {}},
        {
            "step": 2,
            "reasoning": "successful retry",
            "action": {"tool_calls": [{"name": "press_buttons", "args": {"buttons": ["A"]}}]},
        },
        {"step": 3, "reasoning": "third", "action": {}},
    ]
    trajectory.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    details = discover_step_details(tmp_path)

    assert [detail["step"] for detail in details] == [1, 2, 3]
    assert details[1]["analysis"] == "successful retry"
    assert select_recent_step_details(details, 2) == details[-2:]


def test_step_history_api_preloads_recent_and_lazy_loads_one_step(tmp_path):
    manager = RunDataManager(run_id="history-test", base_dir=str(tmp_path))
    rows = [
        {
            "step": step,
            "reasoning": f"analysis {step}",
            "action": {"tool_calls": [{"name": "press_buttons", "args": {"buttons": ["A"]}}]},
        }
        for step in range(1, 31)
    ]
    (manager.run_dir / "trajectory_history.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    client = TestClient(app)

    with patch(
        "utils.data_persistence.run_data_manager.get_run_data_manager",
        return_value=manager,
    ):
        recent_response = client.get("/api/steps/recent?limit=25")
        detail_response = client.get("/api/steps/12")
        missing_response = client.get("/api/steps/99")

    assert recent_response.status_code == 200
    assert recent_response.json()["count"] == 30
    assert [row["step"] for row in recent_response.json()["steps"]] == list(range(6, 31))
    assert detail_response.json()["analysis"] == "analysis 12"
    assert detail_response.json()["buttons"] == ["A"]
    assert missing_response.status_code == 404


def test_timeline_lazily_loads_selected_step_details():
    page = Path("server/timeline.html").read_text(encoding="utf-8")

    assert 'class="step-detail"' in page
    assert "fetch(`/api/steps/${step}`" in page
    assert "detailAbortController.abort()" in page
    assert "loadStepDetail(frame.step)" in page


def test_live_view_hydrates_history_and_pairs_keypresses_with_steps():
    page = Path("server/stream.html").read_text(encoding="utf-8")

    assert "/api/steps/recent?limit=${maxAgentMessages}" in page
    assert "maxAgentMessages = 25" in page
    assert "renderStepActions" in page
    assert "initializeAgentStreaming(newestPersistedStep)" in page
    assert "after_step" in page
    assert "agentThinkingDiv.innerHTML" not in page


def test_live_stream_cursor_does_not_drop_a_step_completed_during_hydration():
    assert _interaction_precedes_live_cursor({"agent_step": 25}, 25) is True
    assert _interaction_precedes_live_cursor({"agent_step": 26}, 25) is False
    assert _interaction_precedes_live_cursor({"agent_step": None}, 25) is True
    assert _interaction_precedes_live_cursor({"agent_step": 26}, None) is True

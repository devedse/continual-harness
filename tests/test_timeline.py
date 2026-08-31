import base64
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.app import app
from server.timeline import (
    discover_timeline_frames,
    resolve_timeline_frame,
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

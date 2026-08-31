import base64

from utils.data_persistence.run_data_manager import RunDataManager


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
    "x8AAwMCAO7Z0ioAAAAASUVORK5CYII="
)
PNG_BASE64 = base64.b64encode(PNG_BYTES).decode("ascii")


def test_save_step_screenshot_uses_numbered_run_directory_path(tmp_path):
    manager = RunDataManager(run_id="1", base_dir=str(tmp_path))

    saved_path = manager.save_step_screenshot(7, PNG_BASE64)

    assert saved_path == tmp_path / "1" / "screenshots" / "step_000007.png"
    assert saved_path.read_bytes() == PNG_BYTES


def test_save_step_screenshot_accepts_data_url_and_replaces_retry(tmp_path):
    manager = RunDataManager(run_id="1", base_dir=str(tmp_path))
    path = manager.save_step_screenshot(2, "data:image/png;base64," + PNG_BASE64)
    replacement = b"replacement-image-data"

    replaced_path = manager.save_step_screenshot(
        2, base64.b64encode(replacement).decode("ascii")
    )

    assert replaced_path == path
    assert path.read_bytes() == replacement


def test_save_step_screenshot_is_non_fatal_for_invalid_data(tmp_path):
    manager = RunDataManager(run_id="1", base_dir=str(tmp_path))

    saved_path = manager.save_step_screenshot(3, "not valid base64")

    assert saved_path is None
    assert not (tmp_path / "1" / "screenshots" / "step_000003.png").exists()

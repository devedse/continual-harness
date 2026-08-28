from server import app as server_app


class _FakeEnvironment:
    def __init__(self, x, y, location="RedsHouse2f"):
        self.x = x
        self.y = y
        self.location = location

    def get_player_position(self):
        return {"x": self.x, "y": self.y}

    def get_map_location(self):
        return self.location


def _entry(action_id, button="UP"):
    return {
        "action_id": action_id,
        "button": button,
        "start_pos": None,
        "end_pos": None,
        "completed": False,
    }


def test_repeated_buttons_update_their_exact_history_entries(monkeypatch):
    fake_env = _FakeEnvironment(3, 6)
    first = _entry(101)
    second = _entry(102)
    monkeypatch.setattr(server_app, "env", fake_env)
    monkeypatch.setattr(server_app, "recent_button_presses", [first, second])

    server_app._record_action_start({"action_id": 101, "button": "UP"})
    fake_env.x, fake_env.y = 3, 5
    server_app._record_action_completion({"action_id": 101, "button": "UP"})

    server_app._record_action_start({"action_id": 102, "button": "UP"})
    fake_env.x, fake_env.y = 3, 4
    server_app._record_action_completion({"action_id": 102, "button": "UP"})

    assert first["start_pos"] == (3, 6, "RedsHouse2f")
    assert first["end_pos"] == (3, 5, "RedsHouse2f")
    assert second["start_pos"] == (3, 5, "RedsHouse2f")
    assert second["end_pos"] == (3, 4, "RedsHouse2f")
    assert first["completed"] is True
    assert second["completed"] is True


def test_blocked_action_records_same_start_and_end(monkeypatch):
    fake_env = _FakeEnvironment(0, 2)
    blocked = _entry(201)
    monkeypatch.setattr(server_app, "env", fake_env)
    monkeypatch.setattr(server_app, "recent_button_presses", [blocked])

    action = {"action_id": 201, "button": "UP"}
    server_app._record_action_start(action)
    server_app._record_action_completion(action)

    assert blocked["start_pos"] == (0, 2, "RedsHouse2f")
    assert blocked["end_pos"] == (0, 2, "RedsHouse2f")


def test_action_completion_helpers_ignore_legacy_queue_entries(monkeypatch):
    fake_env = _FakeEnvironment(3, 6)
    tracked = _entry(301)
    monkeypatch.setattr(server_app, "env", fake_env)
    monkeypatch.setattr(server_app, "recent_button_presses", [tracked])

    server_app._record_action_start("UP")
    server_app._record_action_completion("UP")

    assert tracked["start_pos"] is None
    assert tracked["end_pos"] is None
    assert tracked["completed"] is False


def test_action_completion_waits_for_release_frames():
    action = {"action_id": 401, "button": "UP"}

    pending, completed, remaining = server_app._schedule_action_completion(action, 8)

    assert pending is action
    assert completed is None
    assert remaining == 8


def test_action_completion_with_zero_release_is_immediate():
    action = {"action_id": 402, "button": "UP"}

    pending, completed, remaining = server_app._schedule_action_completion(action, 0)

    assert pending is None
    assert completed is action
    assert remaining == 0

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from utils.server_startup import (
    ServerPortUnavailable,
    assert_server_port_available,
    wait_for_server_port_available,
    wait_for_server_startup,
)


def test_port_check_rejects_an_existing_listener():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        with pytest.raises(ServerPortUnavailable, match="Another process or port-forward rule"):
            assert_server_port_available("127.0.0.1", port)
    finally:
        listener.close()


def test_port_check_accepts_an_available_port():
    assert_server_port_available("127.0.0.1", 0)


def test_port_release_wait_retries_ownerless_network_state(monkeypatch):
    attempts = iter(
        [
            ServerPortUnavailable("still draining"),
            ServerPortUnavailable("still draining"),
            None,
        ]
    )

    def check_port(_host, _port):
        result = next(attempts)
        if result:
            raise result

    monkeypatch.setattr("utils.server_startup.assert_server_port_available", check_port)
    monkeypatch.setattr("utils.server_startup.time.sleep", lambda _seconds: None)

    available, reason = wait_for_server_port_available(
        "0.0.0.0", 8000, timeout=10, poll_interval=0
    )

    assert available is True
    assert reason == ""


def _start_health_server(startup_token):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(
                {"status": "healthy", "startup_token": startup_token}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_health_handshake_accepts_only_the_expected_child():
    server, thread = _start_health_server("expected-token")
    process = MagicMock()
    process.poll.return_value = None
    try:
        ready, reason = wait_for_server_startup(
            process,
            server.server_port,
            "expected-token",
            timeout=1,
            poll_interval=0.01,
        )
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    assert ready is True
    assert reason == ""


def test_health_handshake_rejects_a_different_server():
    server, thread = _start_health_server("someone-elses-token")
    process = MagicMock()
    process.poll.return_value = None
    try:
        ready, reason = wait_for_server_startup(
            process,
            server.server_port,
            "expected-token",
            timeout=1,
            poll_interval=0.01,
        )
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    assert ready is False
    assert "different server or forwarding rule" in reason


def test_wsl_forwarding_avoids_a_wildcard_listener():
    script = Path("scripts/configure_wsl_lan.ps1").read_text()

    assert "listenaddress=$address" in script
    assert "portproxy add v4tov4 listenaddress=0.0.0.0" not in script
    assert "WSL mirrored networking detected; no portproxy is required." in script
    assert "[string]$FirewallProfile = 'Any'" in script
    assert "-RemoteAddress LocalSubnet" in script

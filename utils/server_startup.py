"""Helpers for starting the game server without accepting a false-positive startup."""

from __future__ import annotations

import json
import secrets
import socket
import time
from subprocess import Popen, TimeoutExpired
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


class ServerPortUnavailable(RuntimeError):
    """Raised when the requested server address cannot be bound."""


def assert_server_port_available(host: str, port: int) -> None:
    """Fail before emulator startup when the web address is already reserved."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    bind_host = host
    if host in ("", "*"):
        bind_host = "::" if family == socket.AF_INET6 else "0.0.0.0"

    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((bind_host, port))
    except OSError as exc:
        raise ServerPortUnavailable(
            f"Cannot bind the web server to {bind_host}:{port}: {exc}. "
            "Another process or port-forward rule is using that port."
        ) from exc
    finally:
        probe.close()


def server_port_has_listener(port: int) -> bool:
    """Return whether localhost currently accepts TCP connections on the port."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.25)
    try:
        return probe.connect_ex(("127.0.0.1", port)) == 0
    finally:
        probe.close()


def wait_for_server_port_available(
    host: str,
    port: int,
    timeout: float = 300.0,
    poll_interval: float = 1.0,
) -> tuple[bool, str]:
    """Wait for ownerless kernel state from a removed forwarding rule to drain."""
    deadline = time.monotonic() + timeout
    last_error = "port is unavailable"
    while time.monotonic() < deadline:
        try:
            assert_server_port_available(host, port)
            return True, ""
        except ServerPortUnavailable as exc:
            last_error = str(exc)
        time.sleep(poll_interval)
    return False, last_error


def new_server_startup_token() -> str:
    """Create a token that distinguishes this child from any existing server."""
    return secrets.token_urlsafe(24)


def wait_for_server_startup(
    process: Popen,
    port: int,
    startup_token: str,
    timeout: float = 120.0,
    poll_interval: float = 0.25,
) -> tuple[bool, str]:
    """Wait for the exact child server to answer its health endpoint."""
    health_url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    last_error = "server did not answer"

    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            return False, f"server process exited with code {exit_code}"

        try:
            with urlopen(health_url, timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            observed_token = payload.get("startup_token")
            if observed_token == startup_token:
                return True, ""
            return (
                False,
                "port is answering from a different server or forwarding rule "
                f"(startup token {observed_token!r})",
            )
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = str(exc)

        time.sleep(poll_interval)

    return False, f"timed out after {timeout:.0f}s ({last_error})"


def stop_failed_server(process: Popen) -> None:
    """Stop a child that stayed alive after its HTTP thread failed."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except TimeoutExpired:
        process.kill()
        process.wait(timeout=2)

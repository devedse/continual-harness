from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import run
from server import app as server_app


def test_fastapi_server_binds_all_interfaces_by_default():
    with patch.object(server_app.uvicorn, "run") as uvicorn_run:
        server_app.run_fastapi_server(8123)

    assert uvicorn_run.call_args.kwargs["host"] == "0.0.0.0"
    assert uvicorn_run.call_args.kwargs["port"] == 8123


def test_run_passes_configured_host_to_server_process():
    args = SimpleNamespace(
        port=8123,
        host="0.0.0.0",
        game="red",
        record=False,
        load_checkpoint=False,
        load_state=None,
        no_ocr=False,
        direct_objectives=None,
        scaffold="pokeagent",
    )
    process = MagicMock(pid=1234)

    with patch.object(run.subprocess, "Popen", return_value=process) as popen, patch.object(
        run, "assert_server_port_available"
    ) as port_check, patch.object(
        run, "new_server_startup_token", return_value="test-startup-token"
    ), patch.object(
        run, "wait_for_server_startup", return_value=(True, "")
    ) as wait_for_startup:
        assert run.start_server(args) is process

    port_check.assert_called_once_with("0.0.0.0", 8123)
    wait_for_startup.assert_called_once_with(process, 8123, "test-startup-token")
    command = popen.call_args.args[0]
    assert command[:7] == [
        run.sys.executable,
        "-m",
        "server.app",
        "--host",
        "0.0.0.0",
        "--port",
        "8123",
    ]
    assert popen.call_args.kwargs["env"]["POKEAGENT_SERVER_STARTUP_TOKEN"] == "test-startup-token"

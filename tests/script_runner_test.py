"""Unit tests for shared script runner."""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

import integrations.script_runner as script_runner
from integrations.script_runner import communicate_with_timeout, run_python_script


class _Proc:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_run_python_script_missing_file_logs_error(tmp_path: Path):
    logger = MagicMock()
    missing = tmp_path / "missing.py"

    await run_python_script(
        logger=logger,
        script_path=missing,
        script_name="missing.py",
        timeout_seconds=1,
        log_prefix="Radarr",
    )

    logger.error.assert_called_once()
    assert "script not found" in logger.error.call_args.args[0]


@pytest.mark.asyncio
async def test_run_python_script_timeout_logs_error(tmp_path: Path, monkeypatch):
    logger = MagicMock()
    script = tmp_path / "ok.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    process = MagicMock()
    process.communicate = AsyncMock(return_value=(b"", b""))
    process.wait = AsyncMock()
    signal_process = MagicMock()
    monkeypatch.setattr(script_runner, "_signal_process", signal_process)

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return process

    async def _fake_wait_for(coro, timeout):
        coro.close()
        raise TimeoutError()

    monkeypatch.setattr(
        "integrations.script_runner.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr("integrations.script_runner.asyncio.wait_for", _fake_wait_for)

    await run_python_script(
        logger=logger,
        script_path=script,
        script_name="ok.py",
        timeout_seconds=3,
        log_prefix="Sonarr",
    )

    logger.error.assert_called_once()
    assert "timed out" in logger.error.call_args.args[0]
    assert signal_process.call_args_list == [call(process), call(process, force=True)]
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
async def test_timeout_and_cancellation_clean_up_before_propagating(failure, monkeypatch):
    process = MagicMock()
    process.communicate = AsyncMock(return_value=(b"", b""))
    process.wait = AsyncMock()
    signal_process = MagicMock()
    monkeypatch.setattr(script_runner, "_signal_process", signal_process)
    timeouts = []

    async def wait_for(coro, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            coro.close()
            raise failure()
        return await coro

    monkeypatch.setattr(script_runner.asyncio, "wait_for", wait_for)
    with pytest.raises(failure):
        await communicate_with_timeout(process, 3)

    assert timeouts == [3, script_runner.PROCESS_TERMINATION_GRACE_SECONDS]
    assert signal_process.call_args_list == [call(process), call(process, force=True)]
    process.wait.assert_awaited_once()


@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("exited", [False, True])
def test_process_signaling_respects_platform_and_lingering_group(
    platform, force, exited, monkeypatch
):
    killpg = MagicMock()
    monkeypatch.setattr(script_runner, "os", SimpleNamespace(name=platform, killpg=killpg))
    monkeypatch.setattr(script_runner, "signal", SimpleNamespace(SIGTERM=15, SIGKILL=9))
    process = MagicMock(pid=12345, returncode=0 if exited else None)

    script_runner._signal_process(process, force=force)

    if platform == "posix":
        killpg.assert_called_once_with(12345, 9 if force else 15)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
    elif exited:
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
    elif force:
        process.kill.assert_called_once()
        process.terminate.assert_not_called()
    else:
        process.terminate.assert_called_once()
        process.kill.assert_not_called()


def test_missing_process_group_during_cleanup_is_harmless(monkeypatch):
    killpg = MagicMock(side_effect=ProcessLookupError())
    monkeypatch.setattr(script_runner, "os", SimpleNamespace(name="posix", killpg=killpg))
    monkeypatch.setattr(script_runner, "signal", SimpleNamespace(SIGTERM=15, SIGKILL=9))
    process = MagicMock(pid=12345)

    script_runner._signal_process(process)
    script_runner._signal_process(process, force=True)
    assert killpg.call_count == 2


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX process groups")
async def test_timeout_stops_descendant_holding_output_pipe(monkeypatch):
    child_code = (
        "import os, signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('child-ready', flush=True); reader, writer = os.pipe(); os.read(reader, 1)"
    )
    parent_code = (
        "import os, subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "print('parent-ready', flush=True); reader, writer = os.pipe(); os.read(reader, 1)"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    monkeypatch.setattr(script_runner, "PROCESS_TERMINATION_GRACE_SECONDS", 0.1)
    try:
        assert process.stdout is not None
        ready = {
            await asyncio.wait_for(process.stdout.readline(), timeout=5),
            await asyncio.wait_for(process.stdout.readline(), timeout=5),
        }
        assert ready == {b"parent-ready\n", b"child-ready\n"}
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(communicate_with_timeout(process, 1), timeout=5)
        assert process.returncode is not None
    finally:
        script_runner._signal_process(process, force=True)
        await process.communicate()


@pytest.mark.asyncio
async def test_run_python_script_launch_failure_logs_error(tmp_path: Path, monkeypatch):
    logger = MagicMock()
    script = tmp_path / "ok.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    launch_error = OSError("executable unavailable")

    async def _fake_create_subprocess_exec(*args, **kwargs):
        raise launch_error

    monkeypatch.setattr(
        "integrations.script_runner.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    await run_python_script(
        logger=logger,
        script_path=script,
        script_name="ok.py",
        timeout_seconds=3,
        log_prefix="Sonarr",
    )

    logger.error.assert_called_once_with(
        "%s script failed to launch (%s): %s",
        "Sonarr",
        "ok.py",
        launch_error,
    )


@pytest.mark.asyncio
async def test_run_python_script_success_logs_info(tmp_path: Path, monkeypatch):
    logger = MagicMock()
    script = tmp_path / "ok.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    async def _fake_create_subprocess_exec(*args, **kwargs):
        assert kwargs["start_new_session"] == (script_runner.os.name == "posix")
        return _Proc(returncode=0, stdout=b"done\n")

    async def _fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(
        "integrations.script_runner.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr("integrations.script_runner.asyncio.wait_for", _fake_wait_for)

    await run_python_script(
        logger=logger,
        script_path=script,
        script_name="ok.py",
        timeout_seconds=3,
        log_prefix="Tautulli",
    )

    logger.info.assert_any_call("%s script succeeded: %s", "Tautulli", "ok.py")
    logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_run_python_script_failure_logs_stdout_and_stderr(tmp_path: Path, monkeypatch):
    logger = MagicMock()
    script = tmp_path / "bad.py"
    script.write_text("print('bad')\n", encoding="utf-8")

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _Proc(returncode=2, stdout=b"bad out", stderr=b"bad err")

    async def _fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(
        "integrations.script_runner.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr("integrations.script_runner.asyncio.wait_for", _fake_wait_for)

    await run_python_script(
        logger=logger,
        script_path=script,
        script_name="bad.py",
        timeout_seconds=3,
        log_prefix="Radarr",
    )

    logger.error.assert_any_call("%s script failed (%s) rc=%s", "Radarr", "bad.py", 2)
    logger.error.assert_any_call("[%s stdout] %s", "bad.py", "bad out")
    logger.error.assert_any_call("[%s stderr] %s", "bad.py", "bad err")

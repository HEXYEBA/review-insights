import os
import subprocess
import sys

import pytest

from review_insights.models import ModelTimeoutError
from review_insights import processes


def run_args(tmp_path, code, **overrides):
    arguments = {
        "stdin_text": "",
        "cwd": tmp_path,
        "timeout_s": 5,
        "env": os.environ.copy(),
        "stderr_path": tmp_path / "stderr.log",
    }
    arguments.update(overrides)
    return [sys.executable, "-c", code], arguments


def test_child_exit_zero_writes_requested_stdout(tmp_path):
    stdout_path = tmp_path / "stdout.log"
    args, options = run_args(
        tmp_path, "print('ok')", stdout_path=stdout_path
    )
    assert processes.run_child(args, **options) == 0
    assert stdout_path.read_text(encoding="utf-8").strip() == "ok"


def test_child_nonzero_exit_code_keeps_stderr_private_in_file(tmp_path):
    args, options = run_args(
        tmp_path,
        "import sys; print('safe diagnostic', file=sys.stderr); sys.exit(7)",
    )
    assert processes.run_child(args, **options) == 7
    assert options["stderr_path"].read_text(encoding="utf-8").strip() == "safe diagnostic"


@pytest.mark.parametrize("polling", [False, True])
def test_child_timeout_terminates_its_process(tmp_path, monkeypatch, polling):
    started = []
    real_popen = subprocess.Popen

    def capture_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, (list, tuple)) and command[0] == sys.executable:
            started.append(process)
        return process

    monkeypatch.setattr(processes.subprocess, "Popen", capture_popen)
    args, options = run_args(tmp_path, "import time; time.sleep(20)", timeout_s=0.3)
    if polling:
        options["on_tick"] = lambda: None
    with pytest.raises(ModelTimeoutError):
        processes.run_child(args, **options)
    assert len(started) == 1
    assert started[0].poll() is not None


def test_child_polling_delivers_snapshots_before_exit_and_writes_stdin_once(tmp_path):
    ticks = []
    marker = tmp_path / "ready"
    args, options = run_args(
        tmp_path,
        "import sys,time,pathlib; data=sys.stdin.read(); "
        "pathlib.Path('ready').write_text(data); time.sleep(1.3); print(data)",
        stdin_text="request", stdout_path=tmp_path / "out",
        on_tick=lambda: ticks.append(marker.read_text() if marker.exists() else None),
    )
    assert processes.run_child(args, **options) == 0
    assert "request" in ticks
    assert (tmp_path / "out").read_text().strip() == "request"

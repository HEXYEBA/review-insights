"""Start bounded child processes and stop only the process tree we own."""

import os
import signal
import subprocess
from pathlib import Path
from time import monotonic
from typing import Callable

from review_insights.models import ModelTimeoutError


def _terminate_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
            )
        except OSError:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            process.kill()

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_child(
    args: list[str],
    *,
    stdin_text: str,
    cwd: Path,
    timeout_s: float,
    env: dict[str, str],
    stderr_path: Path,
    stdout_path: Path | None = None,
    on_tick: Callable[[], None] | None = None,
) -> int:
    """Run a child without a shell, logging output to private files."""
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    if stdout_path is not None:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)

    creationflags = 0
    popen_options: dict[str, object] = {}
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_options["start_new_session"] = True

    with stderr_path.open("wb") as stderr_file:
        if stdout_path is None:
            stdout_target = subprocess.DEVNULL
            stdout_context = None
        else:
            stdout_context = stdout_path.open("wb")
            stdout_target = stdout_context

        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=stdout_target,
                stderr=stderr_file,
                cwd=str(cwd),
                env=env,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                **popen_options,
            )
            try:
                if on_tick is None:
                    process.communicate(input=stdin_text, timeout=timeout_s)
                else:
                    deadline = monotonic() + timeout_s
                    pending_input = stdin_text
                    while True:
                        remaining = deadline - monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(args, timeout_s)
                        try:
                            process.communicate(input=pending_input, timeout=min(0.5, remaining))
                            on_tick()
                            break
                        except subprocess.TimeoutExpired:
                            # communicate retains input state across timeout retries.
                            pending_input = None
                            on_tick()
            except subprocess.TimeoutExpired as exc:
                _terminate_tree(process)
                raise ModelTimeoutError(
                    f"Дочерний процесс превысил лимит {timeout_s:g} секунд."
                ) from exc
            except BaseException:
                if process.poll() is None:
                    _terminate_tree(process)
                raise
            return int(process.returncode)
        finally:
            if stdout_context is not None:
                stdout_context.close()

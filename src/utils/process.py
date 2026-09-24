# src/utils/process.py
"""
One place where TaxaTag launches external programs.

Every stage calls run_tool() rather than subprocess directly, so that console
window suppression on Windows, log capture, and Stop-button cancellation all
behave identically no matter which binary is being driven.
"""

from __future__ import annotations

import queue
import re
import subprocess
import threading
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from src.utils.reporting import PipelineCancelled, Reporter


class ToolTimeout(Exception):
    """Raised when a tool outlived the time it was given."""


class ToolStalled(ToolTimeout):
    """
    Raised when a tool's own progress stopped advancing for longer than it
    was allowed. Unlike a timeout, it says nothing about how long the work
    may take - only that the work stopped moving.
    """

#: How long to wait for the output reader to finish after the process has
#: exited. Anything still holding the pipe open past this is a stray worker,
#: not output worth waiting for.
READER_GRACE_SECONDS = 2.0


def _no_window_kwargs() -> dict:
    """
    Stop a console window flashing up for every subprocess on Windows.

    Without this, a GUI run of 100 samples pops up hundreds of black windows.
    On macOS and Linux there is nothing to suppress.
    """
    if sys.platform.startswith("win"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return {
            "startupinfo": startupinfo,
            "creationflags": subprocess.CREATE_NO_WINDOW,
        }
    return {}


@dataclass
class ToolResult:
    """Outcome of a single external command."""

    command: List[str]
    returncode: int
    output: str
    seconds: float
    log_path: Optional[Path] = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def tail(self, lines: int = 5) -> str:
        """The last few lines of output, for error messages."""
        return "\n".join(self.output.strip().splitlines()[-lines:])


def run_tool(
    command: Sequence[str],
    log_path: Optional[Path] = None,
    reporter: Optional[Reporter] = None,
    poll_seconds: float = 0.25,
    echo: bool = False,
    heartbeat: Optional[str] = None,
    heartbeat_seconds: float = 30.0,
    cwd: Optional[Path] = None,
    timeout_seconds: Optional[float] = None,
    progress: Optional[Callable[[], Tuple[float, str]]] = None,
    progress_seconds: float = 300.0,
    stall_seconds: Optional[float] = None,
) -> ToolResult:
    """
    Run an external command, capturing its output.

    stderr is folded into stdout because the tools TaxaTag drives (cutadapt,
    VSEARCH, BLAST) write their real reports to stderr, and users expect one
    readable log per sample rather than two interleaved streams.

    If the reporter reports cancellation the child process is terminated and
    PipelineCancelled is raised.

    Pass `heartbeat` for a step that can run silently for a long time - a
    remote database search may sit in a queue for an hour - so the interface
    keeps saying how long it has been waiting instead of looking frozen.

    Pass `timeout_seconds` to put an upper bound on that wait. Without one, a
    tool that never returns is indistinguishable from one that is merely slow,
    and there is nothing for a retry to react to: the call simply does not
    end. Reaching the limit terminates the process and raises ToolTimeout, so
    the caller can retry or move on.

    Pass `progress` for work that is long by nature - cutting a whole
    reference volume can take hours - and should be judged by whether it
    is still moving, not by how long it has taken (Rudie, 24 September
    2026: a long task is fine; one that stops without ending is not). Every
    `progress_seconds` it is asked for a number that grows as the work
    advances and a sentence saying how far it is, and the sentence is
    reported. With `stall_seconds`, a number that has not grown for that
    long terminates the tool and raises ToolStalled. A probe that fails
    tells nothing either way, so it neither reports nor counts as a stall.
    """
    command = [str(part) for part in command]
    started = time.monotonic()

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        **_no_window_kwargs(),
    )

    # Output is drained on its own thread rather than read from this one.
    #
    # Reading the pipe directly here blocks inside readline() until a line
    # arrives, and a tool that goes quiet for minutes therefore blocks the
    # loop that watches for the Stop button. Worse, some tools spawn workers
    # that inherit the pipe: cutadapt run with several threads leaves those
    # workers holding it open after the parent has exited, so the read never
    # reaches end-of-file and the run hangs indefinitely with no output and no
    # way to cancel. With a reader thread, this loop only ever waits on the
    # process itself, which does finish.
    chunks: List[str] = []
    lines: "queue.Queue[Optional[str]]" = queue.Queue()

    def drain() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)

    reader = threading.Thread(target=drain, name="taxatag-tool-output", daemon=True)
    reader.start()

    def save_log() -> None:
        """
        Write down whatever the tool said, however this ends.

        Called from `finally` rather than after a successful return, because a
        search that times out is the one whose output matters most: NCBI
        explains a refusal in the tool's own words, and reaching the limit
        used to discard that explanation and report only "did not answer".
        """
        if log_path is None:
            return
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write("$ " + " ".join(command) + "\n\n")
                handle.write("".join(chunks))
        except OSError:
            pass

    last_heartbeat = started
    last_probe, last_advance, furthest = started, started, None
    try:
        while True:
            try:
                line = lines.get(timeout=poll_seconds)
                if line is not None:
                    chunks.append(line)
                    if echo and reporter is not None:
                        reporter.debug(line.rstrip())
                    continue
            except queue.Empty:
                pass

            if process.poll() is not None and lines.empty():
                break

            if reporter is not None and reporter.cancelled:
                _terminate(process)
                raise PipelineCancelled("Run stopped by user.")

            now = time.monotonic()
            if timeout_seconds is not None and now - started > timeout_seconds:
                _terminate(process)
                said = " ".join(line.strip() for line in chunks[-3:] if line.strip())
                raise ToolTimeout(
                    f"gave up after {_elapsed(now - started)}"
                    + (f" - it had said: {said[:300]}" if said else "")
                )

            if heartbeat and reporter is not None and now - last_heartbeat >= heartbeat_seconds:
                last_heartbeat = now
                reporter.progress(None, f"{heartbeat} ({_elapsed(now - started)} so far)")

            if progress is not None and now - last_probe >= progress_seconds:
                last_probe = now
                try:
                    reached, said = progress()
                except Exception:  # noqa: BLE001 - a probe that fails tells nothing either way
                    last_advance = now
                else:
                    if furthest is None or reached > furthest:
                        furthest, last_advance = reached, now
                    if reporter is not None and said:
                        reporter.info(f"{said} ({_elapsed(now - started)} so far)")
                if stall_seconds is not None and now - last_advance > stall_seconds:
                    _terminate(process)
                    raise ToolStalled(
                        f"stopped: no progress for {_elapsed(now - last_advance)} "
                        f"(after {_elapsed(now - started)} in all)"
                    )
    finally:
        process.wait()
        # The reader is given a moment to finish, then abandoned. It is a
        # daemon thread, so an orphaned worker still holding the pipe cannot
        # keep the program alive.
        reader.join(timeout=READER_GRACE_SECONDS)
        # Closing the pipe is deliberately skipped while that thread is still
        # inside a read: close() waits for the read to return, which is the
        # very thing that is not going to happen. The file object is left to
        # be reclaimed when the program exits.
        if process.stdout is not None and not reader.is_alive():
            try:
                process.stdout.close()
            except OSError:
                pass
        save_log()

    output = "".join(chunks)

    return ToolResult(
        command=command,
        returncode=process.returncode,
        output=output,
        seconds=time.monotonic() - started,
        log_path=log_path,
    )


def _elapsed(seconds: float) -> str:
    """A duration written the way someone waiting would say it."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} seconds"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _terminate(process: subprocess.Popen) -> None:
    """Ask a child process to stop, then insist if it does not."""
    try:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    except OSError:
        pass


def blast_path(path: Path) -> str:
    """
    Render a path for a BLAST tool's command line.

    The BLAST programs parse several of their own options as space-separated
    lists - -db takes a list of databases - so they split a value on
    whitespace themselves, before the filesystem is ever consulted. A path
    such as "C:/Users/Jane Smith/reference/local_12S_db" is therefore read as
    two names and neither is found. Quoting tells BLAST's parser to treat the
    whole value as one. Paths without spaces are left exactly as they are.

    Most Windows users have a space in their home folder, so this is the
    ordinary case rather than an edge case.
    """
    text = str(path)
    return f'"{text}"' if " " in text else text


def remove_quietly(path: Optional[Path]) -> bool:
    """
    Delete an intermediate file, tolerating failure.

    On Windows a file cannot be deleted while any process still holds it open,
    and an antivirus scanner or a search indexer will do exactly that for a
    second or two after a tool writes it. Losing a finished analysis because a
    scratch file could not be tidied away would be absurd, so a failure here is
    ignored: the file is simply left in the run folder.
    """
    if path is None:
        return False
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except OSError:
        return False


def tool_version(binary: Path, version_flag: str = "--version") -> Optional[str]:
    """Return the first line of a tool's version output, or None if it will not run."""
    return command_version([str(binary), version_flag], Path(binary).stem)


def command_version(command: List[str], program: str = "") -> Optional[str]:
    """
    Ask any command what version it is, and return None if it will not run.

    Separate from `tool_version` because not everything TaxaTag runs is a
    binary with a path. Cutadapt is a Python module, started differently in a
    packaged build than from source, and the only way to know it will start is
    to start it the same way the pipeline does.
    """
    try:
        completed = subprocess.run(
            [str(part) for part in command],
            capture_output=True,
            text=True,
            timeout=60,
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (completed.stdout or "") + (completed.stderr or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None

    # A tool that failed can still print plenty, so a non-zero exit means the
    # answer is "it does not run" however talkative it was.
    if completed.returncode != 0:
        return None

    # Some tools lead with a citation rather than their version - VSEARCH asks
    # to be cited before it says which build it is - so prefer a line that
    # names the program alongside a version number.
    program = (program or "").lower()
    for line in lines:
        lowered = line.lower()
        if program in lowered and re.search(r"\d+\.\d+", line):
            return line
    for line in lines:
        if re.search(r"\d+\.\d+", line):
            return line
    return lines[0]


def hand_off(command: List[str], working_directory: Optional[Path] = None) -> bool:
    """
    Start a process that is meant to outlive this one, and do not wait for it.

    The opposite of `run_tool` in every respect, which is why it is a
    separate function rather than a flag on that one: nothing is captured,
    nothing is waited for, and success means only that Windows accepted the
    request to start it.

    It exists for the updater. An installer replacing TaxaTag cannot be a
    child of TaxaTag - a child started the ordinary way is killed with its
    parent, and the parent is about to exit, so the installer would die a
    moment after being asked to replace the thing that killed it.
    `DETACHED_PROCESS` and a new process group are what break that tie.

    Returns False rather than raising. The caller's fallback is to tell the
    user where the file is and let them run it, which is a worse outcome but
    not an error worth a traceback.
    """
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    try:
        subprocess.Popen(
            [str(part) for part in command],
            creationflags=flags,
            close_fds=True,
            cwd=str(working_directory) if working_directory else None,
        )
    except (OSError, ValueError):
        return False
    return True

# src/gui/worker.py
"""
Runs the pipeline on a background thread.

An analysis takes minutes to hours. Running it on the thread that draws the
window would freeze the interface for that whole time, and the operating
system would offer to kill the application as unresponsive. So the work
happens here, and progress comes back as Qt signals, which are delivered
safely to the interface thread.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from src.pipeline.config import PipelineConfig
from src.pipeline.runner import run_pipeline
from src.utils.reporting import Reporter
from src.utils.validate import ValidationReport, validate


class PipelineWorker(QThread):
    """Runs one pipeline, reporting back as it goes."""

    #: (message, level) for each line of the log.
    message = pyqtSignal(str, str)
    #: (percent 0-100 or -1 for "no estimate", description)
    progress = pyqtSignal(int, str)
    #: The finished run's summary dictionary.
    finished_run = pyqtSignal(dict)

    def __init__(
        self,
        config: PipelineConfig,
        stages: Optional[List[str]] = None,
        run_dir: Optional[Path] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        #: Which stages to run. None means all of them, from the beginning.
        self.stages = stages
        #: An existing run folder to continue inside, when resuming. Without
        #: it a run starting past the first stage would look for the most
        #: recent folder, which is not necessarily the one the user picked.
        self.run_dir = run_dir
        self._stop_requested = threading.Event()

    def stop(self) -> None:
        """Ask the run to finish at its next checkpoint."""
        self._stop_requested.set()

    @property
    def stopping(self) -> bool:
        return self._stop_requested.is_set()

    def run(self) -> None:  # called by Qt on the new thread
        reporter = Reporter(
            log_fn=lambda text, level: self.message.emit(text, level),
            progress_fn=self._emit_progress,
            cancel_check=self._stop_requested.is_set,
            verbose=False,
        )
        result = run_pipeline(
            self.config, reporter, stages=self.stages, run_dir=self.run_dir
        )
        self.finished_run.emit(result)

    def _emit_progress(self, fraction: Optional[float], text: str) -> None:
        # -1 asks the interface for a busy indicator rather than a percentage,
        # which is honest about steps whose length cannot be known in advance,
        # such as waiting on a remote database search.
        self.progress.emit(-1 if fraction is None else int(fraction * 100), text)


class ValidationWorker(QThread):
    """
    Runs the pre-flight checks off the interface thread.

    Checking counts files and launches each tool to read its version, which is
    quick but not instant, and on a slow network drive can take seconds.
    """

    completed = pyqtSignal(object)

    def __init__(self, config: PipelineConfig, parent=None):
        super().__init__(parent)
        self.config = config

    def run(self) -> None:
        try:
            report = validate(self.config)
        except Exception as error:  # noqa: BLE001 - report rather than crash
            report = ValidationReport()
            from src.utils.validate import Check

            report.checks.append(
                Check("Checks", "error", f"The checks could not be completed: {error}")
            )
        self.completed.emit(report)


class SelfTestWorker(QThread):
    """
    Runs the self-test off the interface thread.

    It is a real analysis - four samples through every stage - so it takes
    tens of seconds rather than the moment the pre-flight checks take, and
    reports its progress the same way a run does.
    """

    message = pyqtSignal(str, str)
    completed = pyqtSignal(object)

    def __init__(self, config: PipelineConfig, parent=None):
        super().__init__(parent)
        self.config = config

    def run(self) -> None:
        from src.validation import selftest

        reporter = Reporter(log_fn=lambda text, level: self.message.emit(text, level))
        try:
            report = selftest.run(self.config, reporter)
        except Exception as error:  # noqa: BLE001 - report rather than crash
            from src.utils.validate import Check

            report = ValidationReport()
            report.checks.append(
                Check(
                    "Self-test",
                    "error",
                    f"The self-test could not be completed: {error}",
                    fix="The log above shows how far it got.",
                )
            )
        self.completed.emit(report)


class LibraryDownloadWorker(QThread):
    """
    Fetches a reference library off the interface thread.

    Two gigabytes over a domestic connection is tens of minutes, which is
    long enough that the window must stay usable and long enough that a user
    will want to stop and resume. Both are why this is a thread and not a
    modal wait.

    `stop()` asks rather than kills. The download is checked between chunks
    and leaves its partly-finished file behind on purpose, so stopping costs
    seconds rather than the whole evening - a cancel that threw the progress
    away would simply never be used.
    """

    #: bytes so far, bytes expected, what is happening
    progress = pyqtSignal(int, int, str)
    #: the installed folder on success, or "" if it was stopped
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from src.reference import download

        try:
            installed = download.install(
                self.entry,
                progress=lambda done, total, what: self.progress.emit(done, total, what),
                should_stop=lambda: self._stop,
            )
        except download.DownloadProblem as problem:
            self.failed.emit(str(problem))
            return
        except Exception as error:  # noqa: BLE001 - report rather than crash
            self.failed.emit(f"The download could not be completed: {error}")
            return
        self.completed.emit(str(installed) if installed else "")

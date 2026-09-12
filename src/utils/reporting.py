# src/utils/reporting.py
"""
Progress reporting shared by every pipeline stage.

The stages know nothing about how they are being run. They push messages and
progress into a Reporter; the terminal runner prints them, and the GUI turns
them into log lines and a progress bar. This keeps the science code free of
any GUI imports, which also keeps PyInstaller bundles small and testable.
"""

from __future__ import annotations

import sys
from typing import Callable, Optional


class PipelineCancelled(Exception):
    """Raised when the user asks a running pipeline to stop."""


class Reporter:
    """
    Collects log lines and progress updates from a running stage.

    Parameters
    ----------
    log_fn:
        Called with (message, level) for every log line. Defaults to printing.
    progress_fn:
        Called with (fraction, message) where fraction is 0.0-1.0, or None
        when the stage cannot estimate how far along it is.
    cancel_check:
        Called with no arguments; return True to abort the run at the next
        checkpoint.
    """

    LEVELS = ("debug", "info", "warning", "error", "success")

    #: The mark a line carries for its level - in the terminal, in the
    #: window, and in a log saved to a file, all the same. The window colours
    #: the line as well, but colour must not be the only signal: a reader
    #: who cannot tell amber from black, or who is reading the saved file,
    #: gets the same mark. Levels not listed carry none.
    PREFIXES = {"warning": "[!] ", "error": "[ERROR] ", "success": "[OK] "}

    @classmethod
    def marked(cls, message: str, level: str) -> str:
        return cls.PREFIXES.get(level, "") + message

    def __init__(
        self,
        log_fn: Optional[Callable[[str, str], None]] = None,
        progress_fn: Optional[Callable[[Optional[float], str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        verbose: bool = False,
    ) -> None:
        self._log_fn = log_fn
        self._progress_fn = progress_fn
        self._cancel_check = cancel_check
        self.verbose = verbose

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        if level == "debug" and not self.verbose:
            return
        if self._log_fn is not None:
            self._log_fn(message, level)
        else:
            stream = sys.stderr if level == "error" else sys.stdout
            indent = "      " if level == "debug" else "  "
            print(f"{indent}{self.marked(message, level)}", file=stream, flush=True)

    def debug(self, message: str) -> None:
        self.log(message, "debug")

    def info(self, message: str) -> None:
        self.log(message, "info")

    def warning(self, message: str) -> None:
        self.log(message, "warning")

    def error(self, message: str) -> None:
        self.log(message, "error")

    def success(self, message: str) -> None:
        self.log(message, "success")

    def heading(self, message: str) -> None:
        """A stage boundary. Rendered prominently by the GUI."""
        self.log(message, "heading")

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------
    def progress(self, fraction: Optional[float], message: str = "") -> None:
        if fraction is not None:
            fraction = max(0.0, min(1.0, float(fraction)))
        if self._progress_fn is not None:
            self._progress_fn(fraction, message)

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------
    @property
    def cancelled(self) -> bool:
        return bool(self._cancel_check and self._cancel_check())

    def checkpoint(self) -> None:
        """Abort the run here if the user has pressed Stop."""
        if self.cancelled:
            raise PipelineCancelled("Run stopped by user.")


#: A Reporter that prints to the terminal, for command-line use.
def console_reporter(verbose: bool = False) -> Reporter:
    return Reporter(verbose=verbose)

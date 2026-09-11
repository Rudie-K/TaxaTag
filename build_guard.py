#!/usr/bin/env python3
"""
Refuse to build over a copy of TaxaTag that is currently running.

PyInstaller deletes the whole of `dist/TaxaTag/` before it writes the new one.
If anything in there is locked - and every file a running application has open
is locked on Windows - the delete or the copy fails part way through, and what
is left behind is neither the old build nor the new one. In practice that
means an installation with its `bin/` folder missing: the window still opens,
and then reports that VSEARCH and BLAST "could not be run", which sounds like
a broken machine rather than a half-finished build.

That has happened, so the build now checks first. The check is deliberately
here rather than inside TaxaTag itself: the application has no business
knowing how it is packaged, and a developer running the build is the only
person this can help.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

#: The application's own executable, which is what a running copy holds open.
EXECUTABLE_NAMES = ("TaxaTag.exe", "TaxaTag")


def executable_in(folder: Path) -> Optional[Path]:
    """The built executable inside a bundle folder, if it is there."""
    for name in EXECUTABLE_NAMES:
        candidate = Path(folder) / name
        if candidate.is_file():
            return candidate
    return None


def is_locked(path: Path) -> bool:
    """
    True when a file cannot be written to because something else has it.

    Opening for writing is the test rather than asking the operating system
    for a list of handles: it asks precisely the question the build needs
    answered, needs no extra package, and behaves the same way on Windows,
    where a running image is locked, and on Linux, which reports "text file
    busy" for the same reason.
    """
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError as error:
        # 26 is ETXTBSY: the file is a running program.
        return getattr(error, "errno", None) == 26
    except Exception:  # noqa: BLE001 - anything unexpected is not a lock
        return False


def running_processes() -> List[str]:
    """
    Any running TaxaTag processes, named so the message can be specific.

    Best effort: if the platform's process tool is missing or answers oddly,
    the lock test above has already decided the outcome and this only adds
    detail to the explanation.
    """
    try:
        if sys.platform.startswith("win"):
            output = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq TaxaTag.exe", "/NH"],
                capture_output=True, text=True, timeout=15,
            ).stdout
            return [
                line.split()[1]
                for line in output.splitlines()
                if line.strip().lower().startswith("taxatag.exe")
            ]
        output = subprocess.run(
            ["pgrep", "-f", "TaxaTag"], capture_output=True, text=True, timeout=15
        ).stdout
        return [line.strip() for line in output.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError, IndexError):
        return []


def refuse_if_in_use(folder: Path) -> None:
    """
    Stop the build before anything is deleted, if the target is in use.

    Raises SystemExit, which is what a spec file needs: PyInstaller reads the
    spec before it touches `dist/`, so leaving here costs nothing and leaves
    the existing installation exactly as it was.
    """
    folder = Path(folder)
    executable = executable_in(folder)
    if executable is None or not is_locked(executable):
        return

    processes = running_processes()
    who = (
        f"process ID {', '.join(processes)}"
        if processes
        else "another program"
    )
    raise SystemExit(
        "\n"
        "Build stopped: TaxaTag is still running.\n"
        "\n"
        f"  {executable}\n"
        f"  is held open by {who}.\n"
        "\n"
        "Building would delete that folder first and then fail part way "
        "through, leaving an installation with its bundled tools missing - "
        "which shows up later as 'VSEARCH could not be run'.\n"
        "\n"
        "Close TaxaTag and run the build again. Nothing has been changed.\n"
    )


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist") / "TaxaTag"
    refuse_if_in_use(target)
    print(f"{target} is free to rebuild.")

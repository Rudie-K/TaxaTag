"""
Asking the filesystem about a path without being thrown out of the window.

`Path.exists()`, `is_dir()`, `is_file()`, `resolve()`, `iterdir()` and
`glob()` all read like questions and are not. Each suppresses only the
failures pathlib considers ordinary - ENOENT, ENOTDIR, EBADF, ELOOP and three
Windows equivalents - and **re-raises everything else**. A permissions
refusal, a share that is not mounted, or Windows declining to follow a
junction it did not create (WinError 448) all come straight through.

That crashed the installed application on startup three separate times, in
three separate places, each time on a path the user had chosen and saved:
first the library folder in `discover()`, then the same folder in a fallback
`resolve()` three lines later, then the reads folder in `apply_config()`. Each
fix guarded one call. This module guards the class.

Every helper here answers the question or answers "no", and never raises.
That is the right contract for a program deciding what to *show*: a folder
the program may not look inside is offered as absent, and the pre-flight
check is where the refusal gets explained in words. It is the wrong contract
for a program deciding what to *delete*, which is why `src/utils/safety.py`
does not use these and asks first.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple


def exists(path) -> bool:
    """Whether `path` is there, treating a refusal to say as "no"."""
    try:
        return Path(path).exists()
    except OSError:
        return False


def is_dir(path) -> bool:
    """Whether `path` is a directory, treating a refusal to say as "no"."""
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def is_file(path) -> bool:
    """Whether `path` is a file, treating a refusal to say as "no"."""
    try:
        return Path(path).is_file()
    except OSError:
        return False


def resolve(path) -> Path:
    """
    The real location of `path`, or `path` itself if that cannot be found.

    Returning the original rather than raising suits every caller here:
    `resolve` is used to recognise two names for one folder, and failing
    that comparison costs a duplicate entry in a list. Failing to open at all
    costs the program.
    """
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)


def iterdir(path) -> List[Path]:
    """The contents of `path`, sorted, or nothing if it cannot be listed."""
    try:
        return sorted(Path(path).iterdir())
    except OSError:
        return []


def glob(path, pattern: str) -> List[Path]:
    """Matches for `pattern` under `path`, or nothing if it cannot be read."""
    try:
        return list(Path(path).glob(pattern))
    except OSError:
        return []


def probe_dir(path) -> Tuple[bool, str]:
    """
    Whether `path` is a directory, and why that could not be answered.

    For the callers that want to *say* something about a refused folder
    rather than quietly treat it as absent. The reason is the operating
    system's own wording, which `src/reference/library.py` turns into a
    sentence a user can act on.
    """
    try:
        return Path(path).is_dir(), ""
    except OSError as problem:
        return False, str(problem)

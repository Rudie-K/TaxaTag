# src/utils/platform.py
"""
Everything that differs between Windows, macOS and Linux lives here.

Two jobs: finding the command-line tools that ship inside TaxaTag, and finding
the per-user folder where settings and caches belong on each system.
"""

from __future__ import annotations

import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

#: Folder name under bin/ for each platform, and the executable suffix there.
_PLATFORMS = {
    "win": ("win64", ".exe"),
    "linux": ("linux", ""),
    "darwin": ("macos", ""),
}


def platform_key() -> str:
    """'win', 'linux' or 'darwin' for the machine this is running on."""
    for key in _PLATFORMS:
        if sys.platform.startswith(key):
            return key
    raise OSError(
        f"TaxaTag does not support this operating system ({sys.platform}). "
        "Windows, macOS and Linux are supported."
    )


def app_root() -> Path:
    """
    The folder that holds bin/ and resources/.

    In a packaged build PyInstaller unpacks these into a temporary folder and
    points sys._MEIPASS at it. Running from source, it is the project root.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=None)
def get_bundled_bin(tool_name: str) -> Path:
    """
    Locate a bundled command-line tool for the current platform.

    The tools are distributed as vendor archives whose internal layout differs
    between projects and platforms, so rather than hard-coding each one the
    platform folder is searched for a matching executable.

    The result is cached because the search walks the whole bin/ folder, which
    holds tens of thousands of files, and every PipelineConfig created would
    otherwise repeat it three times over.
    """
    platform_dir, extension = _PLATFORMS[platform_key()]
    platform_root = app_root() / "bin" / platform_dir
    filename = f"{tool_name}{extension}"

    direct = platform_root / filename
    if direct.exists():
        ensure_executable(direct)
        return direct.resolve()

    if platform_root.exists():
        # rglob finds e.g. bin/win64/vsearch/vsearch.exe or
        # bin/linux/ncbi-blast-2.17.0+/bin/blastn without either being
        # written down anywhere.
        for candidate in sorted(platform_root.rglob(filename)):
            if candidate.is_file():
                # Only ours is made executable. A copy found on the system
                # PATH below belongs to whoever installed it, and changing its
                # permissions would be overstepping.
                ensure_executable(candidate)
                return candidate.resolve()

    # Fall back to a copy installed system-wide. This is the normal case for a
    # developer running from source, and a useful escape hatch for a user who
    # already has these tools.
    from shutil import which

    system = which(tool_name)
    if system:
        return Path(system).resolve()

    return direct.resolve()


def ensure_executable(path: Path) -> None:
    """
    Make sure a bundled tool can actually be run on macOS and Linux.

    Copying a release out of a zip or off a Windows-formatted drive commonly
    loses the executable bit, which turns into a confusing "permission denied"
    much later on. Windows has no such bit.

    Called from `get_bundled_bin` for the tools that ship with TaxaTag, and
    only for those: a copy found on the system PATH belongs to whoever
    installed it. A failure here is not worth stopping for - the tool may
    already be runnable, and if it is not, the caller's own error names the
    tool and the path, which is more use than a traceback from here.
    """
    if sys.platform.startswith("win") or not path.exists():
        return
    try:
        mode = path.stat().st_mode
        if not mode & 0o111:
            path.chmod(mode | 0o755)
    except OSError:
        pass


#: First argument that tells the TaxaTag entry point to run a Python module
#: instead of starting normally. See python_module_command().
RUN_MODULE_FLAG = "--taxatag-run-module"


def python_module_command(module: str) -> list:
    """
    The command that runs one of TaxaTag's own Python modules as a subprocess.

    Cutadapt is a Python package, not a standalone program, so it is normally
    started with `python -m cutadapt`. That breaks in a packaged build: there
    the interpreter has been frozen into TaxaTag.exe, sys.executable points at
    the application itself, and `TaxaTag.exe -m cutadapt` would simply open a
    second copy of the window.

    A packaged build therefore re-enters itself with a flag the entry point
    recognises, and hands off to the module from there. The frozen program is
    both the application and its own interpreter.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, RUN_MODULE_FLAG, module]
    return [sys.executable, "-m", module]


def short_path(path: Path) -> Path:
    """
    A form of a path that contains no spaces, where the system offers one.

    Windows keeps an 8.3 "short name" for every file, turning
    "C:/Users/Jane Smith/Study Notes" into "C:/Users/JANESM~1/STUDYN~1". That
    matters because the BLAST tools split several of their own arguments on
    whitespace: makeblastdb writes its database happily, then fails its final
    self-check because it re-opens the database by an absolute path it has
    just split in two. Running it somewhere with no spaces in the path avoids
    the problem entirely.

    Returns the path unchanged on other systems, and on Windows when no short
    name exists (short-name creation can be disabled on a volume).
    """
    if not sys.platform.startswith("win"):
        return Path(path)
    try:
        import ctypes
        from ctypes import wintypes

        get_short = ctypes.windll.kernel32.GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD

        buffer = ctypes.create_unicode_buffer(4096)
        length = get_short(str(Path(path).resolve()), buffer, 4096)
        if length and buffer.value:
            return Path(buffer.value)
    except (OSError, AttributeError, ValueError):
        pass
    return Path(path)


def user_data_dir() -> Path:
    """
    Where TaxaTag keeps settings and caches for the current user.

    Each platform has its own convention, and putting files in the right place
    means they survive an application update and get backed up as expected.
    """
    key = platform_key()
    if key == "win":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "TaxaTag"
    if key == "darwin":
        return Path.home() / "Library" / "Application Support" / "TaxaTag"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "taxatag"


def user_config_path() -> Path:
    """The settings file the graphical interface reads and writes."""
    return user_data_dir() / "settings.yaml"


def default_cache_path() -> Path:
    """Where the taxonomy cache lives when the user has not chosen a location."""
    return user_data_dir() / "taxonomy_cache.json"


def bundled_default_config() -> Optional[Path]:
    """The example settings file shipped with the application, if present."""
    candidate = app_root() / "resources" / "default_config.yaml"
    if candidate.exists():
        return candidate
    candidate = app_root() / "config.yaml"
    return candidate if candidate.exists() else None


def open_in_file_manager(path: Path) -> bool:
    """
    Show a folder in Explorer, Finder or the desktop's file manager.

    Used by the 'Open results folder' button, which is how most users will
    actually get at their output.
    """
    path = Path(path)
    if not path.exists():
        return False
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa: S606 - the documented Windows API
        elif sys.platform.startswith("darwin"):
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        return True
    except (OSError, subprocess.SubprocessError):
        return False

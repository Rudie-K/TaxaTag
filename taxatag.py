#!/usr/bin/env python3
"""
TaxaTag - environmental DNA to species.

Copyright (C) 2026 Rudie Kauhanen, Lucy Thomas and Ruth Farrant

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program. If not, see <https://www.gnu.org/licenses/>.

Run with no arguments to open the window; pass any option to use the terminal
instead. This is the file to double-click, and the one PyInstaller builds.
"""

import io
import sys
from pathlib import Path

# Make `import src...` work no matter where the program is started from.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# A packaged build has no console, so Python leaves stdout and stderr as None
# and the first print() anywhere raises AttributeError. Somewhere to write is
# substituted so that a stray message is discarded rather than bringing the
# whole application down.
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()


def _run_module(module: str, arguments: list) -> int:
    """
    Run a Python module as though it had been started with `python -m`.

    Used by a packaged build, where there is no separate interpreter to call.
    Three things make this fiddlier than it looks:

    * A frozen build contains only what something imports, and a __main__
      reached through runpy is easy for the packager to miss, so there is a
      fallback to the module's own entry point.
    * Those entry points signal completion by raising SystemExit, which has
      to be caught and turned back into a return code.
    * They do not necessarily return one. Cutadapt's main() returns a
      statistics object, which is truthy, so treating a return value as an
      exit code reports success as failure.

    Every failed attempt is reported, because a packaging fault here stops the
    application trimming anything at all and the cause is otherwise invisible.
    """
    import importlib
    import runpy

    sys.argv = [module] + list(arguments)
    problems = []

    try:
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        return 0
    except SystemExit as finished:
        return int(finished.code or 0)
    except ImportError as error:
        problems.append(f"as a module: {error}")

    for candidate in (f"{module}.cli", module):
        try:
            imported = importlib.import_module(candidate)
        except ImportError as error:
            problems.append(f"{candidate}: {error}")
            continue

        entry = getattr(imported, "main", None)
        if not callable(entry):
            problems.append(f"{candidate}: no main() to call")
            continue

        try:
            entry(arguments)
        except SystemExit as finished:
            return int(finished.code or 0)
        # Reaching here means it finished without raising, which is success.
        # Whatever it returned is a result, not an exit code.
        return 0

    print(f"Could not run '{module}'. Tried:", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


def main() -> int:
    arguments = sys.argv[1:]

    # A packaged build is its own interpreter: sys.executable is TaxaTag.exe,
    # so a helper that would normally be started with `python -m <module>` is
    # started by re-entering this program instead. See
    # src/utils/platform.py: python_module_command.
    if len(arguments) >= 2 and arguments[0] == "--taxatag-run-module":
        return _run_module(arguments[1], arguments[2:])

    # A bare settings file is treated as "open the window with these settings",
    # so that a .yaml can be dragged onto the application.
    only_config = len(arguments) == 1 and arguments[0].lower().endswith((".yaml", ".yml"))

    if arguments and not only_config:
        from src.cli import main as cli_main

        return cli_main(arguments)

    try:
        from src.gui.app import main as gui_main
    except ImportError as error:
        print(f"The graphical interface could not start: {error}", file=sys.stderr)
        print("\nPyQt6 may not be installed. Install it with:", file=sys.stderr)
        print("    pip install PyQt6", file=sys.stderr)
        print("\nYou can still use the terminal interface:", file=sys.stderr)
        print("    python taxatag.py --help", file=sys.stderr)
        return 1

    return gui_main(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())

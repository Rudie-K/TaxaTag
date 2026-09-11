#!/usr/bin/env python3
"""
Build TaxaTag without ever putting the existing installation at risk.

    python build.py

PyInstaller writes straight into `dist/TaxaTag/`, and the first thing it does
is delete what is there. That is fine until something has a file open - and a
running copy of TaxaTag holds every library it has loaded. The delete or the
copy then fails part way through and what is left is neither the old build nor
the new one: an installation whose `bin/` folder is missing, which shows up
much later as "VSEARCH could not be run" and looks nothing like a build
problem. That has happened twice.

Checking that nothing is running before starting is not enough, because a
build takes minutes and somebody can open the application during it. So the
build goes somewhere else entirely and only the last step touches the real
folder:

    1. build into dist/.staging/TaxaTag - nothing else is affected, and this
       is where the minutes are spent
    2. move the existing installation aside
    3. move the new one into place
    4. delete the one moved aside

Steps 2 to 4 are renames within one drive, so they take milliseconds rather
than minutes, and the window in which anything could interfere shrinks to
almost nothing. If even that fails, the finished build is still sitting in
staging and the old installation is still where it was - so the worst case is
that nothing changed, rather than an application with its tools missing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_guard import refuse_if_in_use  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent
SPEC = PROJECT_ROOT / "pyinstaller.spec"
DIST = PROJECT_ROOT / "dist"
STAGING = DIST / ".staging"
BUNDLE_NAME = "TaxaTag"


#: What the finished bundle records about where it came from.
BUILD_STAMP = "build_info.json"


def stamp_build(bundle: Path) -> None:
    """
    Write which commit this build was made from, into the build.

    Without it, "is the installed copy up to date?" can only be answered by
    comparing bytecode module by module - which says *that* something
    differs and never *what changed*. The alternative is a list of pending
    updates kept by hand, and a list kept by hand goes stale the first time
    somebody is in a hurry.

    A dirty tree is recorded as dirty rather than refused. Building from
    uncommitted work is a normal thing to do while testing something;
    recording the last commit as though that were what was built is the part
    that would mislead afterwards.
    """
    import json
    import subprocess
    from datetime import datetime

    def git(*args) -> str:
        try:
            done = subprocess.run(
                ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    info = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "commit": git("rev-parse", "HEAD"),
        "subject": git("log", "-1", "--format=%s"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
    }
    try:
        (bundle / BUILD_STAMP).write_text(json.dumps(info, indent=2), encoding="utf-8")
    except OSError:
        # A build that cannot be stamped is still a build. This is a note
        # about it rather than part of it.
        pass


def compile_into(staging: Path) -> None:
    """Run PyInstaller, writing the finished bundle into a folder of its own."""
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm",
        "--distpath", str(staging),
        "--workpath", str(PROJECT_ROOT / "build"),
        str(SPEC),
    ]
    print("$ " + " ".join(command), flush=True)
    # TAXATAG_SKIP_BUILD_GUARD tells the spec not to check dist/TaxaTag: this
    # build is not going there, and refusing because the application happens
    # to be open would stop a build that cannot possibly disturb it.
    environment = {**dict(__import__("os").environ), "TAXATAG_SKIP_BUILD_GUARD": "1"}
    finished = subprocess.run(command, cwd=PROJECT_ROOT, env=environment)
    if finished.returncode != 0:
        raise SystemExit(
            f"\nPyInstaller failed (exit {finished.returncode}). "
            "Your installation has not been touched.\n"
        )


def swap_into_place(new: Path, target: Path) -> None:
    """
    Replace the installation with the freshly built one.

    Ordered so that the new build is never deleted and the old one is never
    deleted until the new one is in place. The only moment neither is at
    `target` is between two renames.
    """
    if not new.is_dir():
        raise SystemExit(f"\nThe build did not produce {new}.\n")

    refuse_if_in_use(target)

    previous = target.with_name(target.name + ".previous")
    shutil.rmtree(previous, ignore_errors=True)

    try:
        if target.exists():
            target.rename(previous)
        new.rename(target)
    except OSError as error:
        # Put the old one back if the new one could not be moved in, so the
        # failure leaves the machine as it was found.
        if previous.exists() and not target.exists():
            previous.rename(target)
        raise SystemExit(
            f"\nThe new build could not be moved into place: {error}\n\n"
            f"It is complete and waiting at:\n  {new}\n\n"
            "Close TaxaTag and run this again; nothing has been lost.\n"
        ) from error

    shutil.rmtree(previous, ignore_errors=True)


def describe(bundle: Path) -> None:
    """Report what was produced, in the terms that matter if it is wrong."""
    tools = bundle / "_internal" / "bin"
    installed = sorted(p.name for p in tools.rglob("*") if p.is_dir() and p.parent == tools / "win64") \
        if (tools / "win64").is_dir() else []
    total = sum(f.stat().st_size for f in bundle.rglob("*") if f.is_file())
    stamp_build(bundle)
    print(f"\nBuilt {bundle}")
    print(f"  size          {total / 1_048_576:,.0f} MB")
    if installed:
        print(f"  bundled tools {', '.join(installed)}")
    else:
        print("  bundled tools NONE FOUND - this build is not usable")


def main() -> int:
    staged_bundle = STAGING / BUNDLE_NAME
    target = DIST / BUNDLE_NAME

    compile_into(STAGING)
    swap_into_place(staged_bundle, target)
    shutil.rmtree(STAGING, ignore_errors=True)
    describe(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

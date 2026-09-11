#!/usr/bin/env python3
"""
Package a built TaxaTag into an installer, and a portable zip beside it.

    python installer/build_installer.py

Refuses rather than produces something wrong. Everything it checks first has
been got wrong at least once in this project, and each mistake produces an
installer that looks fine and is not:

* a build that does not match the source it should have come from
* a build that has not passed its own self-test
* a version number in the installer that nothing else agrees with

The zip is always produced. It needs no tooling and is what somebody on a
locked-down machine, or on macOS or Linux, actually wants: unpack it and run
the program. The installer needs Inno Setup, which is a free download and is
a build tool rather than something TaxaTag depends on - so its absence is
reported and is not a failure.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "installer" / "taxatag.iss"
BUILT = ROOT / "dist" / "TaxaTag"
OUTPUT = ROOT / "dist" / "installer"

#: Where Inno Setup puts its compiler. Searched rather than configured,
#: because the default install location is the only one most people use.
#:
#: The third entry is the one that is easy to miss. `winget install
#: JRSoftware.InnoSetup` installs per-user by default - no administrator
#: password, which is precisely why somebody on a managed laptop would use
#: winget - and lands here rather than in Program Files. Without it the
#: build reports Inno Setup as absent on a machine that has just installed
#: it, which reads as a broken script rather than a missing path.
ISCC_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    Path.home() / "AppData" / "Local" / "Programs" / "Inno Setup 6" / "ISCC.exe",
]


def find_compiler() -> Path | None:
    for candidate in ISCC_CANDIDATES:
        if candidate.exists():
            return candidate
    found = shutil.which("ISCC")
    return Path(found) if found else None


def version_from_script() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r'#define\s+AppVersion\s+"([^"]+)"', text)
    if match is None:
        raise SystemExit("No AppVersion in taxatag.iss")
    return match.group(1)


def check(message: str, ok: bool, fix: str = "") -> bool:
    print(f"  [{'OK' if ok else 'FAILED'}]  {message}")
    if not ok and fix:
        print(f"           {fix}")
    return ok


def preflight() -> bool:
    """Everything that must be true before an installer is worth building."""
    print("Before packaging:")
    good = True

    good &= check(
        "a build exists",
        (BUILT / "TaxaTag.exe").exists(),
        "Run `python build.py` first.",
    )
    if not good:
        return False

    # The expensive mistake: shipping a build made from code that has since
    # changed. It is invisible in the installer and obvious to whoever
    # reports the bug you already fixed.
    verify = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "verify_build.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    good &= check(
        "the build matches the source",
        verify.returncode == 0,
        "Rebuild: the bundle and the working tree disagree.\n"
        + "           " + verify.stdout.strip().replace("\n", "\n           "),
    )

    # A build that passes the bytecode check can still be a build that does
    # not run - a missing data file, a tool that only fails once frozen.
    print("           running the self-test, this takes a moment...")
    selftest = subprocess.run(
        [str(BUILT / "TaxaTag.exe"), "--self-test"],
        capture_output=True, text=True, timeout=600,
    )
    good &= check(
        "the built application passes its self-test",
        selftest.returncode == 0,
        "The build runs but does not work. See docs/protocols/the-self-test.md.",
    )

    good &= check("a licence is included", (ROOT / "LICENSE").exists())

    # A release carries the application, never a reference library. If that
    # ever changes, the terms of the data have to be checked before it ships:
    # two of the four sources publish no licence at all, so a library bundled
    # today could not lawfully be handed on. Cheaper to refuse here than to
    # recall a download.
    sys.path.insert(0, str(ROOT))
    from src.reference import library as library_module
    from src.reference import licences

    bundled = [
        folder for folder in BUILT.rglob("*")
        if folder.is_dir() and library_module.is_library(folder)
    ]
    if not bundled:
        good &= check("no reference library is bundled", True)
    else:
        shareable = True
        for folder in bundled:
            sources = licences.sources_in(folder / library_module.CATALOGUE_NAME)
            allowed, reasons = licences.may_be_shared(sources)
            shareable &= allowed
            if not allowed:
                print(f"           {folder.name}: " + "; ".join(reasons))
        good &= check(
            "any bundled library may be redistributed",
            shareable,
            "Remove it from the build, or use sources whose terms allow it. "
            "See docs/protocols/reference-data.md rule 12.",
        )

    return bool(good)


def make_zip(version: str) -> Path:
    """The portable form: unpack and run, no installer and no privileges."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / f"TaxaTag-{version}-Windows-x64-portable.zip"
    if target.exists():
        target.unlink()

    files = [p for p in BUILT.rglob("*") if p.is_file()]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, Path("TaxaTag") / path.relative_to(BUILT))
        for extra in ("LICENSE", "README.md"):
            archive.write(ROOT / extra, Path("TaxaTag") / extra)
    return target


def write_checksums(files: List[Path]) -> Path:
    """
    Write a `CHECKSUMS.txt` beside the things that were built.

    The format is the one `sha256sum` writes, so somebody can check a download
    with tools they already have - and so `utils/updates.py` can read it. That
    second reader is why this is not optional. An installed copy of TaxaTag
    will not run an update whose checksum it cannot look up, so a release
    published without this file is a release that silently never offers
    itself. See `docs/decisions/0020`.
    """
    lines = []
    for path in files:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 22), b""):
                digest.update(block)
        lines.append(f"{digest.hexdigest()}  {path.name}")

    # newline="" so Windows does not translate \n into \r\n. This file is
    # read by `sha256sum -c` and its equivalents, which treat the carriage
    # return as part of the file name and then report every line as a file
    # they cannot open. Our own parser survives it, because splitlines()
    # strips \r - which is precisely why the fault was invisible to a test
    # that checked this writer against that reader.
    target = OUTPUT / "CHECKSUMS.txt"
    with open(target, "w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(lines) + "\n")
    return target


def main() -> int:
    if not preflight():
        print("\nNothing packaged.")
        return 1

    version = version_from_script()
    print(f"\nPackaging TaxaTag {version}")

    archive = make_zip(version)
    print(f"  portable   {archive.stat().st_size / 1048576:7.0f} MB  {archive.name}")

    compiler = find_compiler()
    if compiler is None:
        print("\n  Inno Setup was not found, so no installer was built.")
        print("  It is a free download from https://jrsoftware.org/isdl.php")
        print("  The portable zip above is complete and needs no installer.")
        return 0

    finished = subprocess.run([str(compiler), str(SCRIPT)], cwd=ROOT, text=True)
    if finished.returncode != 0:
        print("\n  Inno Setup failed. Its output is above.")
        return 1

    installer = next(OUTPUT.glob(f"TaxaTag-{version}-*Setup.exe"), None)
    if installer:
        print(f"  installer  {installer.stat().st_size / 1048576:7.0f} MB  {installer.name}")
    checksums = write_checksums([f for f in (archive, installer) if f])
    print(f"  checksums               {checksums.name}")
    print("\nAll are in " + str(OUTPUT))
    print("Attach CHECKSUMS.txt to the release beside the installer. An")
    print("installed copy refuses to run an update it cannot verify, so a")
    print("release published without it never offers itself to anybody.")
    print("A reference library is downloaded separately - see the README.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

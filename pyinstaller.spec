# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build description for TaxaTag.

Build the application for whichever platform you are on:

    pyinstaller pyinstaller.spec

The result lands in dist/TaxaTag/ and can be zipped and handed to someone who
has no Python and no bioinformatics tools installed.

A build has to run on the platform it targets: PyInstaller freezes the
interpreter and libraries of the machine doing the building, so producing the
Windows, macOS and Linux releases means running this on each of the three.
"""

import os
import pathlib
import sys
from pathlib import Path

# SPECPATH is set by PyInstaller to the folder holding this file.
PROJECT_ROOT = Path(SPECPATH).resolve()

# Checked here, at the top, because PyInstaller reads this file before it
# touches dist/ - and the first thing it does after that is delete the folder
# this would overwrite. Building over a running copy leaves an installation
# with its bundled tools missing, which surfaces much later as "VSEARCH could
# not be run" and looks nothing like a build problem.
sys.path.insert(0, str(PROJECT_ROOT))
from build_guard import refuse_if_in_use  # noqa: E402
from src.version import __version__ as APP_VERSION  # noqa: E402

# `python build.py` builds into a staging folder and only swaps at the end, so
# it does not touch the installation and sets this to say so. Running
# PyInstaller directly does write over dist/TaxaTag, and is checked.
if not os.environ.get("TAXATAG_SKIP_BUILD_GUARD"):
    refuse_if_in_use(PROJECT_ROOT / "dist" / "TaxaTag")

# Only the current platform's tools are worth carrying. Bundling all three
# would roughly triple the download for no benefit.
if sys.platform.startswith("win"):
    PLATFORM_DIR, APP_ICON = "win64", "resources/taxatag.ico"
elif sys.platform.startswith("darwin"):
    PLATFORM_DIR, APP_ICON = "macos", "resources/taxatag.icns"
else:
    PLATFORM_DIR, APP_ICON = "linux", None

icon_path = PROJECT_ROOT / APP_ICON if APP_ICON else None
if icon_path is not None and not icon_path.exists():
    icon_path = None


#: Folders under bin/ that a frozen build does not need.
#:
#: python_embed is a complete Python installation, carried so that the .bat
#: launcher works on a machine with no Python at all. PyInstaller freezes its
#: own interpreter, so including it as well would add several hundred
#: megabytes that can never be executed.
SKIP_IN_BUNDLE = {"python_embed", "python_env"}

#: The BLAST programs TaxaTag actually launches.
#:
#: The NCBI release ships twenty-four of them and they are statically linked,
#: so each is 13-20 MB and the folder comes to 375 MB. TaxaTag runs five: it
#: searches nucleotides against nucleotides, and never translates, never
#: builds a profile, and never reads an SRA archive through BLAST.
#:
#: Dropping the rest takes 295 MB off every release - about 40% of the whole
#: application - and is the single largest saving available. Nothing else in
#: the bundle is close.
#:
#: The risk is the opposite one: a program that *is* needed and is not listed
#: goes missing only in the built copy, and only when the stage that needs it
#: runs. tests/test_packaging.py compares this list against every
#: get_bundled_bin() call in the source, so adding a call without adding it
#: here fails the suite rather than the release.
KEEP_BLAST_PROGRAMS = {
    "blastn",              # every search
    "makeblastdb",         # building a reference volume
    "blastdbcmd",          # reading one back, and verifying it
    "blastdb_aliastool",   # scopes - decision 0006
    "blast_formatter",     # collecting a finished NCBI search by its id
}

#: Anything not a program keeps its place: the .dll files beside them are
#: shared, and the data/ folder holds substitution matrices BLAST loads by
#: name at run time.
PROGRAM_SUFFIXES = {".exe", ""}


def _blast_files(folder, destination):
    """One BLAST release, with the programs TaxaTag never runs left out."""
    kept = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(folder)
        looks_like_a_program = (
            path.suffix.lower() in PROGRAM_SUFFIXES
            and relative.parts[:1] in ((), ("bin",))
        )
        if looks_like_a_program and path.stem not in KEEP_BLAST_PROGRAMS:
            continue
        kept.append((str(path), f"{destination}/{relative.parent.as_posix()}"))
    return kept


def tool_tree():
    """The bundled command-line tools for this platform, if they are present."""
    source = PROJECT_ROOT / "bin" / PLATFORM_DIR
    if not source.exists():
        print(f"WARNING: no bundled tools found at {source}.")
        print("         The build will rely on VSEARCH, BLAST and the SRA")
        print("         Toolkit being installed on the user's machine.")
        return []

    # Each vendor archive keeps its own internal layout, which the tool lookup
    # in src/utils/platform.py searches, so they are copied whole - except
    # BLAST, which is filtered because most of it is programs for a different
    # kind of search.
    trees = []
    for entry in sorted(source.iterdir()):
        if not entry.is_dir() or entry.name in SKIP_IN_BUNDLE:
            continue
        destination = f"bin/{PLATFORM_DIR}/{entry.name}"
        if entry.name.startswith("ncbi-blast"):
            trees.extend(_blast_files(entry, destination))
        else:
            trees.append((str(entry), destination))
    return trees


#: Resources that exist for the developer, not the program. Copying the
#: folder whole put all of these on every user's machine: a script that
#: generates the icons, the README's screenshot, the source artwork the
#: icons are derived from, and Python's own build cache. None is read at
#: run time and together they are a third of a megabyte of somebody else's
#: business sitting in their Program Files.
DEVELOPER_ONLY = {
    "screenshot-run.png",                  # illustrates the README, nothing else
    "screenshot-results.png",
    "screenshot-dark.png",
}


def resource_files():
    """
    The resources the running program actually needs.

    Named individually rather than copied wholesale, because a folder copy
    ships whatever happens to be in it - including files added later for
    reasons that have nothing to do with the user. `BRANDING.md` is here on
    purpose: it is small, and somebody holding only the installed program
    should be able to read the terms on the name and the logo.
    """
    found = []
    for path in sorted((PROJECT_ROOT / "resources").rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        if path.name in DEVELOPER_ONLY or path.name.startswith("screenshot-"):
            continue
        relative = path.relative_to(PROJECT_ROOT / "resources").parent
        found.append((str(path), str(pathlib.PurePosixPath("resources") / relative)))
    return found


base_datas = tool_tree() + resource_files()

# Cutadapt is started from inside the frozen program rather than imported by
# it, so PyInstaller sees no reference to it and pulls in nothing.
#
# Naming its modules one at a time does not work. Cutadapt is part Python and
# part compiled extension, and a missing piece only shows up at run time as an
# ImportError deep inside the bundle: first cutadapt.__main__, then
# cutadapt._match_tables, and so on. collect_all() takes the submodules, the
# compiled libraries and the data files together, which ends the guessing.
from PyInstaller.utils.hooks import collect_all

hiddenimports = ["pandas", "yaml", "requests"]

#: The self-test is reached only from inside SelfTestWorker.run(), so nothing
#: imports it at module level. PyInstaller does follow function-level imports
#: and would very likely find it anyway - it is named here because the cost of
#: naming it is nothing and the cost of being wrong is a button that raises
#: ImportError in the built copy, on the one screen a user presses when they
#: already suspect the installation is broken.
hiddenimports += ["src.validation", "src.validation.selftest"]

#: Reached today only from the terminal (`python -m src.analysis`), so
#: nothing in the window imports it and PyInstaller would leave it out;
#: the Analysis tab will import it, and the build must match the source
#: it came from before then, not after (`tools/verify_build.py`).
hiddenimports += ["src.analysis", "src.analysis.candidates", "src.analysis.adjudication", "src.analysis.metrics", "src.analysis.__main__"]

#: The same reasoning, and here the cost of being wrong is higher. The
#: update check is reached only from inside MainWindow after the window
#: is up, and the install record only from inside download.install() - so
#: nothing imports either at module level. An ImportError in the built
#: copy would appear as a program that opens and then cannot be updated,
#: which is the one fault that cannot be fixed by shipping an update.
hiddenimports += ["src.gui.update_offer", "src.utils.updates"]
collected_binaries, collected_datas = [], []

for package in ("cutadapt", "dnaio", "xopen", "isal"):
    datas_, binaries_, hidden_ = collect_all(package)
    collected_datas += datas_
    collected_binaries += binaries_
    hiddenimports += hidden_

#: backports.zstd is reached only when xopen opens a .zst file, so nothing
#: imports it where PyInstaller can see. It is named here because its absence
#: does not show up as a missing module at start-up: cutadapt fails on the
#: first sample with "No module named 'backports.zstd._zstd'", by which point
#: the run has already read every input file.
#:
#: Listed separately from the rest because it is optional - a machine without
#: it should still produce a working build.
for optional in ("backports.zstd", "zstandard"):
    try:
        datas_, binaries_, hidden_ = collect_all(optional)
    except Exception:  # noqa: BLE001 - not installed is not a build failure
        continue
    collected_datas += datas_
    collected_binaries += binaries_
    hiddenimports += hidden_

# Qt ships translations, multimedia, WebEngine and more that a form-based
# interface never touches. Dropping them saves a few hundred megabytes.
excludes = [
    "matplotlib", "scipy", "notebook", "IPython", "jupyter", "tkinter",
    "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQuick",
    "PyQt6.QtQml", "PyQt6.QtMultimedia", "PyQt6.Qt3DCore", "PyQt6.QtBluetooth",
    "PyQt6.QtNetworkAuth", "PyQt6.QtPositioning", "PyQt6.QtSensors",
]

a = Analysis(
    [str(PROJECT_ROOT / "taxatag.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=collected_binaries,
    datas=base_datas + collected_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TaxaTag",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX corrupts some Qt libraries and trips antivirus
    console=False,      # a double-clicked science tool should not open a terminal
    disable_windowed_traceback=False,
    argv_emulation=sys.platform.startswith("darwin"),  # lets files be dropped on the icon
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="TaxaTag",
)

# macOS expects an application bundle rather than a folder of files.
if sys.platform.startswith("darwin"):
    app = BUNDLE(
        coll,
        name="TaxaTag.app",
        icon=str(icon_path) if icon_path else None,
        bundle_identifier="org.taxatag.app",
        info_plist={
            "CFBundleName": "TaxaTag",
            "CFBundleDisplayName": "TaxaTag",
            "CFBundleShortVersionString": APP_VERSION,
            "NSHighResolutionCapable": True,
            # Without this, macOS Ventura and later show the application as
            # not responding while a long analysis runs.
            "LSBackgroundOnly": False,
        },
    )

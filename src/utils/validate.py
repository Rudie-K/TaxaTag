# src/utils/validate.py
"""
Pre-flight checks.

These run before a single read is processed, because the alternative is a user
waiting forty minutes to be told that a folder was empty. Every failure says
what is wrong and what to do about it, in words that do not assume the reader
writes software.

Checks are scoped to what a particular run actually needs: a run that is not
converting SRA files is not blocked by a missing SRA toolkit, and a run
searching NCBI over the internet is not blocked by a missing local database.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from src.pipeline.config import PipelineConfig
from src.pipeline.stage0_standardise import find_sra_files
from src.utils import paths
from src.utils.platform import python_module_command
from src.utils.process import command_version, tool_version

#: A run needs at least this much free space to be worth starting. Trimmed and
#: merged intermediates roughly match the size of the input.
MIN_FREE_GB = 2.0

#: Windows cannot open a file whose full path is longer than this from an
#: ordinary program, however deep a folder Python was able to create.
MAX_WINDOWS_PATH = 260

#: Roughly how many characters TaxaTag adds below the results folder:
#: runs/<timestamp>/<stage folder>/<locus>/<sample>_<suffix>.fastq.gz
RUN_FOLDER_ALLOWANCE = 95


@dataclass
class Check:
    """The outcome of one pre-flight check."""

    name: str
    status: str          # "ok", "warning" or "error"
    message: str
    fix: str = ""        # what the user should do about it

    @property
    def blocking(self) -> bool:
        return self.status == "error"


@dataclass
class ValidationReport:
    """Everything the checks found, and whether the run may proceed."""

    checks: List[Check] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(c.status == "error" for c in self.checks):
            return "error"
        if any(c.status == "warning" for c in self.checks):
            return "warning"
        return "ok"

    @property
    def can_run(self) -> bool:
        return self.status != "error"

    @property
    def problems(self) -> List[Check]:
        return [c for c in self.checks if c.status != "ok"]

    @property
    def summary(self) -> str:
        if self.status == "ok":
            return "Everything checks out."
        errors = sum(1 for c in self.checks if c.status == "error")
        warnings = sum(1 for c in self.checks if c.status == "warning")
        if errors:
            return f"{errors} problem(s) must be fixed before the run can start."
        return f"Ready to run, with {warnings} thing(s) worth knowing."

    def as_dict(self) -> dict:
        """Plain-data view, for logs and tests."""
        return {
            "status": self.status,
            "summary": self.summary,
            "checks": [
                {"name": c.name, "status": c.status, "message": c.message, "fix": c.fix}
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_python_packages() -> Check:
    """Confirm the Python libraries the pipeline relies on are present."""
    # Only what the pipeline genuinely imports. Listing anything else makes a
    # packaged build fail its own checks over a library it never uses:
    # Biopython was inherited from the original pipeline's requirements and
    # nothing here has ever called it, since TaxaTag parses FASTA and handles
    # sequences itself in src/utils/sequences.py.
    required = {
        "pandas": "pandas",
        "yaml": "pyyaml",
        "requests": "requests",
        "cutadapt": "cutadapt",
    }
    missing = []
    for module, package in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if missing:
        return Check(
            "Python packages",
            "error",
            f"Missing: {', '.join(missing)}.",
            fix=f"Install them with:  pip install {' '.join(missing)}",
        )
    return Check("Python packages", "ok", "All required packages are installed.")


#: Libraries Cutadapt pulls in as it starts, rather than TaxaTag importing
#: them itself.
#:
#: Each of these has been missing from a packaged build at some point, and the
#: absence never shows at start-up. It shows on the first sample, as
#: "No module named 'backports.zstd._zstd'" or similar, by which point the run
#: has already read every input file and the user reasonably believes their
#: setup was checked. They are named here so a failure can say which one.
TRIMMER_LIBRARIES = ("cutadapt", "dnaio", "xopen", "isal", "backports.zstd")


def _importable(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:  # noqa: BLE001 - a library that raises is as absent as one missing
        return False


def check_read_trimmer() -> Check:
    """
    Confirm Cutadapt will start, by starting it exactly as the pipeline does.

    Importing it here would prove less than it appears to. A packaged build
    runs Cutadapt as a subprocess of itself, through
    `platform.python_module_command`, and that path can fail on its own -
    a missing compiled extension, or the re-entry flag not reaching the entry
    point - while the import inside this process succeeds. Running it is the
    only check that covers what the run will actually do.
    """
    version = command_version(
        python_module_command("cutadapt") + ["--version"], "cutadapt"
    )
    if version:
        return Check("Read trimmer (Cutadapt)", "ok", f"Cutadapt {version}")

    missing = [name for name in TRIMMER_LIBRARIES if not _importable(name)]
    if missing:
        return Check(
            "Read trimmer (Cutadapt)",
            "error",
            f"Cutadapt could not be started. Missing: {', '.join(missing)}.",
            fix=(
                "These ship with TaxaTag, so a packaged copy is incomplete - "
                "reinstall it. Running from source, install them with:  "
                f"pip install {' '.join(name.split('.')[0] for name in missing)}"
            ),
        )
    return Check(
        "Read trimmer (Cutadapt)",
        "error",
        "Cutadapt is installed but would not start.",
        fix=(
            "Every library it needs is present, so the problem is in how it is "
            "launched. Run 'python -m cutadapt --version' in a terminal to see "
            "what it says."
        ),
    )


@dataclass
class PlannedRun:
    """
    What pressing Start would actually do, before it is pressed.

    The window shows what a run will be performed *on* - two folders - and
    nothing about what will be done to it. The marker set is a tab away and
    the reference library is two, so the settings that decide whether a run
    takes forty seconds or four hours, and whether it can name a species at
    all, are invisible at the moment of committing to them.

    That is how forty minutes gets spent on a run that was never going to be
    right: not through an error, but through a default nobody looked at.
    `Check my setup` knows all of this already and is behind a button, which
    means it is read by people who suspect a problem rather than by people
    about to cause one.

    Lives here rather than in the window because it is a statement about the
    analysis, and `docs/scopes.md` keeps those out of `gui/`. The window
    formats it; it does not work it out.
    """

    samples: int
    #: Files that carry no R1/R2 marker, counted separately because they are
    #: usually a naming problem rather than single-end data.
    unpaired: int
    sra: int
    markers: List[str] = field(default_factory=list)
    #: Where identification will happen, in words.
    searching: str = ""
    #: True when a library on this machine will be used, which is the
    #: difference between seconds and hours.
    offline: bool = False
    #: Roughly how long this would take, from past runs on this machine.
    #: None until there has been one, because a guess dressed as a
    #: measurement is worse than saying nothing.
    seconds: Optional[float] = None

    def describe(self) -> str:
        """One line, ordered by what would surprise somebody most."""
        if not self.samples and not self.unpaired and not self.sra:
            return "No sequencing files found yet - choose the folder holding your reads."

        parts = []
        if self.samples:
            parts.append(f"{self.samples} sample{'s' if self.samples != 1 else ''}")
        if self.sra:
            parts.append(f"{self.sra} SRA file{'s' if self.sra != 1 else ''} to convert")
        if self.unpaired:
            parts.append(f"{self.unpaired} unpaired file{'s' if self.unpaired != 1 else ''}")

        markers = ", ".join(self.markers) if self.markers else "no primer sets switched on"
        line = f"{' + '.join(parts)}  ·  {markers}  ·  {self.searching}"
        if self.seconds:
            line += f"  ·  {describe_duration(self.seconds)}"
        return line


def _past_rates(output_base) -> List[float]:
    """
    Seconds per unit of work from finished runs, newest first.

    A unit is one sample on one marker, because the stages that dominate -
    trimming and dereplication - run once per sample per locus. Dividing by
    that is what makes an old run predict a differently-shaped new one; the
    bare duration only ever predicts a repeat of itself.

    Runs from before this was recorded contribute nothing rather than
    contributing a guess, which is why the estimate appears after the first
    run on a given machine rather than immediately.
    """
    from src.pipeline import layout

    rates: List[float] = []
    for folder in layout.runs_by_recency(output_base)[:10]:
        try:
            manifest = json.loads((folder / "run_manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        timing = manifest.get("timing") or {}
        seconds = timing.get("seconds_total") or 0
        work = (timing.get("samples") or 0) * max(1, timing.get("markers") or 0)
        if seconds > 0 and work > 0:
            rates.append(seconds / work)
    return rates


def estimate_seconds(config: PipelineConfig, samples: int, markers: int) -> Optional[float]:
    """
    Roughly how long a run would take, or None when there is nothing to go on.

    The median of past rates rather than the mean, because one run that was
    interrupted, or that queued behind NCBI for an hour, would otherwise drag
    every later estimate with it. A median needs three runs to be shaken by
    one bad one, and by then the machine really has got slower.
    """
    work = samples * max(1, markers)
    if work <= 0:
        return None
    rates = _past_rates(config.output_base)
    if not rates:
        return None
    rates.sort()
    middle = rates[len(rates) // 2]
    return middle * work


def describe_duration(seconds: float) -> str:
    """
    A duration as somebody would say it, rounded to its own precision.

    "About 18 minutes" and not "1080 seconds": the estimate is not accurate
    to the second and should not pretend to be, because a number given
    precisely is a number that gets believed precisely.
    """
    if seconds < 90:
        return "under 2 minutes"
    minutes = seconds / 60
    if minutes < 60:
        return f"about {int(round(minutes / 5) * 5) or 5} minutes"
    hours = minutes / 60
    if hours < 10:
        return f"about {hours:.0f} hour{'s' if round(hours) != 1 else ''}"
    return "several hours"


def plan_run(config: PipelineConfig) -> PlannedRun:
    """
    Summarise what a run would do, cheaply enough to call on every keystroke.

    Nothing here reads a sequencing file. Samples are counted by pairing
    *names*, using the same `_classify_read` the pipeline itself uses - a
    second implementation would eventually disagree with the first, and the
    number in front of the user would stop being the number that runs.
    """
    from src.pipeline.stage0_standardise import _classify_read

    plan = PlannedRun(samples=0, unpaired=0, sra=0)

    folder = Path(config.input_dir) if config.input_dir else None
    if folder:
        try:
            names = [
                path.name for pattern in ("*.fastq", "*.fastq.gz", "*.fq", "*.fq.gz")
                for path in folder.glob(pattern)
            ]
            plan.sra = len(find_sra_files(folder))
        except OSError:
            # A folder that cannot be read is not a summary problem. The
            # pre-flight check says so properly; this just shows nothing.
            names = []

        paired: dict = {}
        for name in names:
            direction, sample = _classify_read(name)
            if direction is None:
                plan.unpaired += 1
            else:
                paired.setdefault(sample, set()).add(direction)
        plan.samples = sum(1 for halves in paired.values() if halves == {"R1", "R2"})
        # A sample with only one half is not a sample yet.
        plan.unpaired += sum(1 for halves in paired.values() if halves != {"R1", "R2"})

    plan.markers = sorted({
        locus.get("marker", "") for locus in config.active_loci() if locus.get("marker")
    })

    if config.blast_mode == "reference" and config.reference_dir:
        from src.reference.library import ReferenceLibrary, cannot_be_read

        refused = cannot_be_read(config.reference_dir)
        if refused:
            plan.searching = "the chosen library cannot be read just now"
        else:
            library = ReferenceLibrary(Path(config.reference_dir))
            if library.exists:
                total = sum(library.counts_by_marker().values())
                plan.searching = f"searching {Path(config.reference_dir).name} on this computer ({total:,} sequences)"
                plan.offline = True
            else:
                plan.searching = "no reference library at the chosen folder"
            library.close()
    elif config.blast_mode == "reference":
        plan.searching = "no reference library chosen"
    elif config.blast_mode == "local":
        plan.searching = "searching a BLAST database on this computer"
        plan.offline = True
    else:
        plan.searching = "searching NCBI over the web - slower, and needs a connection"

    plan.seconds = estimate_seconds(config, plan.samples, len(plan.markers))
    return plan


def check_input(config: PipelineConfig) -> Check:
    """Confirm there is something to analyse."""
    if not config.input_dir or not paths.exists(config.input_dir):
        return Check(
            "Input folder",
            "error",
            f"The input folder does not exist: {config.input_dir}",
            fix="Choose the folder that holds your sequencing files.",
        )
    if not paths.is_dir(config.input_dir):
        return Check(
            "Input folder",
            "error",
            f"The input path is a file, not a folder: {config.input_dir}",
            fix="Choose the folder that contains your files, not one of the files.",
        )

    sra_files = find_sra_files(config.input_dir)
    fastq_files = [
        f
        for pattern in ("*.fastq", "*.fastq.gz", "*.fq", "*.fq.gz")
        for f in Path(config.input_dir).glob(pattern)
    ]

    if not sra_files and not fastq_files:
        return Check(
            "Input folder",
            "error",
            f"No sequencing files were found in {config.input_dir}.",
            fix=(
                "TaxaTag reads .fastq and .fastq.gz files, and SRA files "
                "downloaded from NCBI. Check the folder is the right one."
            ),
        )

    parts = []
    if fastq_files:
        parts.append(f"{len(fastq_files)} FASTQ file(s)")
    if sra_files:
        parts.append(f"{len(sra_files)} SRA file(s)")
    found = " and ".join(parts)

    if sra_files and not config.convert_sra:
        return Check(
            "Input folder",
            "warning",
            f"Found {found}, but SRA conversion is switched off so the SRA files "
            "will be ignored.",
            fix="Switch on 'Convert SRA files' to include them.",
        )
    if not fastq_files and sra_files:
        return Check("Input folder", "ok", f"Found {found}, which will be converted first.")
    return Check("Input folder", "ok", f"Found {found}.")


def check_output(config: PipelineConfig) -> Check:
    """Confirm results can actually be written, with room to spare."""
    output = Path(config.output_base)
    try:
        output.mkdir(parents=True, exist_ok=True)
        probe = output / ".taxatag_write_test"
        probe.touch()
        probe.unlink()
    except (OSError, PermissionError) as error:
        return Check(
            "Results folder",
            "error",
            f"Results cannot be written to {output} ({error}).",
            fix="Choose a folder you can write to, such as one inside your Documents.",
        )

    try:
        free_gb = shutil.disk_usage(output).free / (1024 ** 3)
    except OSError:
        return Check("Results folder", "ok", f"Results will be written to {output}.")

    # Windows refuses to open a path longer than 260 characters from an
    # ordinary program, and TaxaTag's own run folders add around 90 characters
    # to whatever the user chose. Python creates such folders happily, so the
    # failure surfaces much later as a tool being unable to write a file.
    if sys.platform.startswith("win"):
        headroom = MAX_WINDOWS_PATH - len(str(paths.resolve(output))) - RUN_FOLDER_ALLOWANCE
        if headroom < 0:
            return Check(
                "Results folder",
                "error",
                f"The path to {output} is too long. Windows cannot open files "
                f"more than {MAX_WINDOWS_PATH} characters deep, and this run "
                f"would need about {-headroom} more than that.",
                fix="Choose a results folder closer to the top of the drive, such as C:/TaxaTag.",
            )
        if headroom < 30:
            return Check(
                "Results folder",
                "warning",
                f"The path to {output} leaves only {headroom} characters before "
                f"Windows' {MAX_WINDOWS_PATH}-character limit.",
                fix="A results folder nearer the top of the drive would be safer.",
            )

    if free_gb < MIN_FREE_GB:
        return Check(
            "Results folder",
            "warning",
            f"Only {free_gb:.1f} GB of free space on this drive.",
            fix="Free up some space, or choose a folder on a drive with more room.",
        )
    return Check(
        "Results folder", "ok", f"{output} is writable ({free_gb:.0f} GB free)."
    )


def _check_binary(name: str, path: Path, version_flag: str, fix: str) -> Check:
    """Confirm one bundled command-line tool is present and will run."""
    version = tool_version(path, version_flag)
    if version:
        return Check(name, "ok", version)

    # Fall back to a copy installed system-wide, which is how a developer
    # running from source usually has these.
    system_path = shutil.which(Path(path).name)
    if system_path:
        version = tool_version(Path(system_path), version_flag)
        if version:
            return Check(name, "ok", f"{version} (installed on this computer)")

    return Check(name, "error", f"{name} could not be run. Looked in {path}.", fix=fix)


def check_tools(config: PipelineConfig) -> List[Check]:
    """Check only the external tools this particular run will actually use."""
    checks = [
        _check_binary(
            "VSEARCH",
            config.vsearch_path,
            "--version",
            "VSEARCH ships with TaxaTag. Reinstalling should restore it.",
        )
    ]

    if config.convert_sra and find_sra_files(config.input_dir):
        checks.append(
            _check_binary(
                "SRA Toolkit",
                config.fastqdump_path,
                "--version",
                "Switch off 'Convert SRA files' if your data is already in FASTQ format.",
            )
        )

    checks.append(
        _check_binary(
            "BLAST",
            config.blastn_path,
            "-version",
            "BLAST ships with TaxaTag. Reinstalling should restore it.",
        )
    )
    return checks


def check_loci(config: PipelineConfig) -> Check:
    """Confirm the primer sets are complete enough to trim with."""
    if not config.loci:
        return Check(
            "Primer sets",
            "error",
            "No primer sets are configured.",
            fix="Add at least one primer set describing the region you sequenced.",
        )

    active = config.active_loci()
    if not active:
        return Check(
            "Primer sets",
            "error",
            f"All {len(config.loci)} primer sets are switched off, so no sequences "
            "would be recognised.",
            fix="Tick at least one primer set in the Primer sets tab.",
        )

    # Only the ticked sets are checked. One that is switched off may well be
    # half-finished on purpose, and refusing to run because of a set nobody
    # asked for would be the check getting in the way rather than helping.
    required = ("forward", "reverse", "forward_rc", "reverse_rc")
    for locus in active:
        name = locus.get("name", "unnamed")
        primers = locus.get("primers") or {}
        missing = [key for key in required if not primers.get(key)]
        if missing:
            return Check(
                "Primer sets",
                "error",
                f"Primer set '{name}' is missing: {', '.join(missing)}.",
                fix=(
                    "Every primer set needs the forward and reverse primers and "
                    "the reverse complement of each."
                ),
            )
        for key in ("min_len", "max_len", "q_score", "error_rate"):
            if key not in locus:
                return Check(
                    "Primer sets",
                    "error",
                    f"Primer set '{name}' is missing the '{key}' setting.",
                    fix="Fill in every field for this primer set.",
                )

    names = ", ".join(locus.get("name", "unnamed") for locus in active)
    switched_off = len(config.loci) - len(active)
    tail = f" {switched_off} switched off." if switched_off else ""
    return Check("Primer sets", "ok", f"{len(active)} in use: {names}.{tail}")


def check_library_licence(config: PipelineConfig) -> Check:
    """
    Say what the reference library in use may be used for.

    Reported alongside the other checks because it is a fact about this run
    that the user cannot otherwise see: the terms live in a file beside the
    data, and nobody opens a 2 GB folder to read a text file. Somebody doing
    consultancy work needs to know before the report goes out, not after.

    Never blocking. Whether a particular job counts as commercial is not a
    question this program can answer, and refusing to run would be answering
    it. The check states the terms and leaves the judgement where it belongs.
    """
    if config.blast_mode != "reference" or not config.reference_dir:
        return Check(
            "Reference data terms", "ok",
            "Not using a reference library, so no data terms apply.",
        )

    from src.reference import licences

    root = Path(config.reference_dir)
    notice = root / licences.TERMS_FILE
    # paths.exists, because this file sits behind whatever the user chose
    # as a library folder - and a junction Windows will not follow raised
    # here, taking every other pre-flight check down with it.
    if not paths.exists(notice):
        return Check(
            "Reference data terms",
            "warning",
            "This library does not record where its data came from, so what "
            "you may do with the results cannot be stated.",
            fix=(
                "Libraries built by TaxaTag carry a licence.json. One that "
                "does not predates that, or came from somewhere else - check "
                "with whoever provided it before using it commercially."
            ),
        )

    import json

    try:
        with open(notice, "r", encoding="utf-8") as handle:
            terms = json.load(handle)
    except (OSError, ValueError):
        return Check(
            "Reference data terms", "warning",
            f"{licences.TERMS_FILE} could not be read.",
        )

    sources = ", ".join(terms.get("sources", [])) or "unrecorded"
    if terms.get("commercial_use"):
        return Check(
            "Reference data terms", "ok",
            f"Built from {sources}. No source forbids commercial use.",
        )
    reasons = "; ".join(terms.get("reasons_against_commercial_use", []))
    return Check(
        "Reference data terms",
        "warning",
        f"Built from {sources}. Not for commercial use: {reasons}.",
        fix=(
            "Research and teaching are unaffected. For paid work, use a "
            f"library built without that source - see {licences.NOTICE_FILE} "
            "in the library folder."
        ),
    )


def check_primer_windows(config: PipelineConfig) -> Check:
    """
    Note where a saved primer set's length window differs from the shipped one.

    A settings file keeps whatever it was saved with, which is right - someone
    may have tuned a window for their own amplicon and should not have it
    overwritten. But it also means a set carries its original numbers forever,
    including ones that have since been corrected.

    That happened here. `MarVer3_16S` shipped with the 232-274 window quoted
    for that primer pair in the literature; measuring 81 real samples showed a
    median of 220 and a range of 189-230, with not one sample inside the
    quoted window, and the preset was corrected to 185-240. A settings file
    written before that correction kept 232-274, and a later run of 111
    samples produced 23 "outside the configured window" notes - every one of
    them the stale number rather than a fault in the data.

    A note rather than a warning: nothing is filtered on these unless the
    length filter is switched on, so the cost is a misleading report and not a
    wrong result. It names both numbers and leaves the choice alone.
    """
    from src.gui.primers import PRESETS

    presets = {p["name"]: p for p in PRESETS}
    drifted = []
    for locus in config.active_loci():
        preset = presets.get(locus.get("name", ""))
        if preset is None:
            continue
        yours = (locus.get("min_len"), locus.get("max_len"))
        shipped = (preset["min_len"], preset["max_len"])
        if None not in yours and yours != shipped:
            drifted.append(
                f"{locus['name']} is set to {yours[0]}-{yours[1]} bp, "
                f"TaxaTag now ships {shipped[0]}-{shipped[1]}"
            )

    if not drifted:
        return Check(
            "Expected amplicon lengths",
            "ok",
            "Every primer set uses the length window it ships with.",
        )
    return Check(
        "Expected amplicon lengths",
        "warning",
        "; ".join(drifted) + ".",
        fix=(
            "Keep yours if you set it deliberately. Otherwise the shipped "
            "window is the one measured on real runs, and using it will stop "
            "the denoising report flagging lengths that are actually normal. "
            "Remove the set and add it again from 'Add a standard set...' to "
            "take the shipped numbers."
        ),
    )


def overlapping_primer_sets(loci) -> list:
    """
    Pairs of primer sets whose forward primers can match the same read.

    TaxaTag decides which marker a sample is by looking for each primer set in
    it, and a sample may legitimately match none. It may not usefully match
    two: it would then be trimmed once per set, and the same reads would be
    counted again under a second locus name in the results.

    Unrelated markers cannot collide - across 111 samples not one matched both
    the 12S and 16S sets. Two primer sets for the *same* marker easily can.
    fwhF2 was derived from the Leray forward primer by widening two ambiguity
    codes, so every read Leray claims, fwhF2 claims as well.

    A pattern cannot be compared against another pattern, so each is tested
    against an example of what the other accepts - which catches the case
    either way round, since the wider primer is the one that matches the
    narrower one's example. Pairs come back in the order the sets were given.
    """
    from src.utils.sequences import example_sequence, primer_pattern

    found = []
    for first_index, first in enumerate(loci):
        for second in loci[first_index + 1:]:
            forwards = (
                (first.get("primers") or {}).get("forward", ""),
                (second.get("primers") or {}).get("forward", ""),
            )
            if not all(forwards):
                continue
            claims = (
                bool(primer_pattern(forwards[1]).match(example_sequence(forwards[0]))),
                bool(primer_pattern(forwards[0]).match(example_sequence(forwards[1]))),
            )
            if any(claims):
                found.append((first.get("name", "unnamed"), second.get("name", "unnamed")))
    return found


def check_primer_overlap(config: PipelineConfig) -> Check:
    """
    Warn when two switched-on primer sets would claim the same samples.

    A warning rather than an error, because it is a legitimate thing to do
    deliberately - comparing two protocols on one batch is a real experiment.
    It is only silent double-counting when nobody meant it.
    """
    active = config.active_loci()
    clashes = overlapping_primer_sets(active)
    if not clashes:
        return Check(
            "Primer sets do not overlap",
            "ok",
            "No two primer sets in use would claim the same reads.",
        )
    described = "; ".join(f"{a} and {b}" for a, b in clashes)
    return Check(
        "Primer sets do not overlap",
        "warning",
        f"These primer sets bind the same place, so samples matching one will "
        f"be counted under both: {described}.",
        fix=(
            "Untick one of each pair in the Primer sets tab, unless you meant "
            "to compare them on the same reads."
        ),
    )


def check_database(config: PipelineConfig) -> Check:
    """Confirm the chosen search method is usable."""
    if config.blast_mode == "reference":
        return _check_reference_library(config)

    if config.blast_mode == "local":
        if not config.blast_db:
            return Check(
                "Reference database",
                "error",
                "Searching a local database is selected, but no database has been chosen.",
                fix="Choose a BLAST database, or switch to searching NCBI online.",
            )
        database = Path(config.blast_db)
        # A BLAST database is a set of files sharing one prefix, so the path
        # given is a name rather than a file that exists on its own.
        if not list(database.parent.glob(database.name + ".*")):
            return Check(
                "Reference database",
                "error",
                f"No BLAST database was found named {database}.",
                fix=(
                    "Point to the database name without an extension, for example "
                    "'C:/blastdb/nt' rather than 'C:/blastdb/nt.ndb'."
                ),
            )
        return Check("Reference database", "ok", f"Local database: {database}")

    if not config.ncbi_email:
        return Check(
            "Reference database",
            "warning",
            "Searching NCBI online without an email address.",
            fix=(
                "NCBI asks for an email address so they can get in touch about "
                "heavy use. Add yours in the settings."
            ),
        )
    return Check(
        "Reference database",
        "ok",
        "Will search NCBI online. This is slower than a local database.",
    )


def _check_reference_library(config: PipelineConfig) -> Check:
    """
    Confirm the reference library is present and covers this run's markers.

    Checked before the run rather than after trimming, because a library that
    holds nothing for the markers in use produces an empty species table after
    an hour of processing, with nothing to say why.
    """
    from src.reference.library import ReferenceLibrary
    from src.reference.markers import marker_for_locus

    if not config.reference_dir:
        return Check(
            "Reference library",
            "error",
            "Using a reference library is selected, but no library folder has been chosen.",
            fix="Choose the folder holding taxatag_reference_core.db, or search NCBI instead.",
        )

    library = ReferenceLibrary(Path(config.reference_dir))
    if not library.exists:
        # A shortcut whose target has gone is the confusing case, because the
        # name is still listed and only the thing behind it is missing. Said
        # separately, because the fix is to put that folder back rather than
        # anything to do with TaxaTag.
        from src.reference.library import broken_links, unreadable_paths

        # Checked before the missing-target case, because a folder that is
        # there and refused looks identical from the outside and the advice
        # is opposite: "restore it from the Recycle Bin" sends somebody
        # hunting for data that was never lost.
        refused = unreadable_paths()
        if refused:
            path, why = refused[0]
            return Check(
                "Reference library",
                "error",
                why,
                fix=(
                    "Nothing is wrong with the library or with TaxaTag. The "
                    "quickest fix is to point TaxaTag straight at the real "
                    "folder with 'Choose a folder...' instead of going "
                    f"through {path.name}."
                ),
            )

        dangling = broken_links()
        if dangling:
            names = ", ".join(str(p) for p in dangling)
            return Check(
                "Reference library",
                "error",
                f"{names} is a shortcut to a folder that is no longer there.",
                fix=(
                    "The library itself has been moved or deleted - check the "
                    "Recycle Bin, or point TaxaTag at wherever it is now. "
                    "Nothing here is wrong with TaxaTag."
                ),
            )
        return Check(
            "Reference library",
            "error",
            f"No reference library was found in {config.reference_dir}.",
            fix=(
                "The folder should contain taxatag_reference_core.db and a "
                "blast_volumes folder."
            ),
        )

    available = set(library.available_markers())
    counts = library.counts_by_marker()
    library.close()

    if not available:
        return Check(
            "Reference library",
            "error",
            "The catalogue was found, but it holds no searchable marker volumes.",
            fix="Rebuild the library, or choose a different folder.",
        )

    needed, unknown = set(), []
    for locus in config.active_loci():
        marker = marker_for_locus(locus)
        if marker:
            needed.add(marker)
        else:
            unknown.append(locus.get("name", "unnamed"))

    described = ", ".join(
        f"{m} ({counts[m]:,})" if m in counts else m for m in sorted(available)
    )

    if unknown:
        return Check(
            "Reference library",
            "warning",
            f"Holds {described}. The marker gene for {', '.join(unknown)} could not be "
            "worked out, so those sequences cannot be identified.",
            fix="Set the marker gene on that primer set.",
        )

    missing = sorted(needed - available)
    if missing and len(missing) == len(needed):
        return Check(
            "Reference library",
            "error",
            f"Holds {described}, but this run needs {', '.join(sorted(needed))}. "
            "Nothing could be identified.",
            fix="Add the missing marker to the library, or search NCBI instead.",
        )
    if missing:
        return Check(
            "Reference library",
            "warning",
            f"Holds {described}. Nothing is available for {', '.join(missing)}, so "
            "those primer sets cannot be identified.",
            fix="Add the missing marker to the library to identify those sequences.",
        )

    return Check("Reference library", "ok", f"Holds {described} reference sequences.")


# ---------------------------------------------------------------------------
# Everything at once
# ---------------------------------------------------------------------------

def _guarded(name: str, run) -> List[Check]:
    """
    Run one check so that its failure is a result rather than an abort.

    A pre-flight check that raises used to take every other check with it:
    the window showed "The checks could not be completed" and nothing else,
    so a single unreadable file - licence.json behind a junction Windows
    would not follow - hid the state of the input folder, the tools, the
    primers and everything else that was perfectly fine and would have told
    the user what to do next.

    The check that failed still reports as a problem, in its own name, with
    the error. It is not swallowed; it is contained.
    """
    try:
        result = run()
    except Exception as error:  # noqa: BLE001 - one check must never hide the rest
        return [Check(
            name, "error",
            f"This check could not be completed: {error}",
            fix=(
                "The other checks below still ran. If this names a folder, "
                "TaxaTag was not allowed to look inside it - see the "
                "reference library check for the usual cause."
            ),
        )]
    return list(result) if isinstance(result, list) else [result]


def validate(config: PipelineConfig) -> ValidationReport:
    """Run every pre-flight check and collect the results."""
    report = ValidationReport()
    for name, run in (
        ("Python packages", check_python_packages),
        ("Read trimmer (Cutadapt)", check_read_trimmer),
        ("Input folder", lambda: check_input(config)),
        ("Results folder", lambda: check_output(config)),
        ("Primer sets", lambda: check_loci(config)),
        ("Primer overlap", lambda: check_primer_overlap(config)),
        ("Primer windows", lambda: check_primer_windows(config)),
        ("Reference data terms", lambda: check_library_licence(config)),
        ("Reference library", lambda: check_database(config)),
        ("Tools", lambda: check_tools(config)),
    ):
        report.checks.extend(_guarded(name, run))
    return report


def validate_all(config: PipelineConfig) -> dict:
    """Dictionary form of :func:`validate`, kept for existing callers."""
    return validate(config).as_dict()


if __name__ == "__main__":
    import sys

    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.yaml")
    if not config_path.exists():
        print(f"No configuration file at {config_path}")
        raise SystemExit(1)

    report = validate(PipelineConfig.from_yaml(config_path))
    print(f"\n{report.summary}\n")
    symbols = {"ok": "  OK  ", "warning": " WARN ", "error": "FAILED"}
    for check in report.checks:
        print(f"[{symbols[check.status]}] {check.name}: {check.message}")
        if check.fix:
            print(f"           -> {check.fix}")
    raise SystemExit(0 if report.can_run else 1)

# src/validation/selftest.py
"""
The self-test: a small analysis with a known answer, run on demand.

    python -m src.validation.selftest --library <folder>

Check my setup answers "are the pieces present". This answers the harder
question: "do they work together". Those come apart more often than they
sound like they should - a bundled Cutadapt that imports but will not start,
a BLAST volume whose files are all there but which nothing can open, a
reference library pointed at the wrong folder. Each of those passes every
check that looks at one thing at a time, and each produces an empty results
table an hour into a real run.

The data is four samples of about a kilobyte each, one per marker, holding
three species apiece. They are synthetic but not invented: every read is a
real amplicon cut from a real reference sequence at the real primer binding
sites, with no sequencing error added. A self-test that fails occasionally
for reasons of its own would teach a user to ignore it.

The answer is therefore known exactly, and the whole thing finishes in under
a minute. See docs/protocols/the-self-test.md for how the data is rebuilt.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from src.pipeline import layout
from src.pipeline.config import PipelineConfig
from src.utils.platform import app_root
from src.utils.reporting import Reporter, console_reporter
from src.utils.validate import (
    MAX_WINDOWS_PATH, RUN_FOLDER_ALLOWANCE, Check, ValidationReport,
)

#: Where the bundled reads and their expected answers live, relative to the
#: folder holding bin/ and resources/. The whole resources tree is copied into
#: a packaged build, so this path is the same frozen or from source.
DATA_FOLDER = "selftest"

MANIFEST_NAME = "manifest.json"

#: The folder a self-test runs in, beneath the user's results folder. Fixed
#: rather than dated: one is enough, and a user who goes looking should find
#: the last one rather than a pile.
WORK_FOLDER = "TaxaTag self-test"


def data_dir() -> Path:
    """Where the bundled self-test data is."""
    return app_root() / "resources" / DATA_FOLDER


def room_to_work(work_dir: Path) -> Optional[Check]:
    """
    Refuse a working folder too deep for Windows to open files inside.

    A normal run is saved from this by the pre-flight checks, which the
    self-test does not run - it uses its own settings, so most of them would
    be checking things it had just decided itself. This one still applies, and
    the self-test is more exposed than a normal run rather than less, because
    it adds a folder of its own beneath whatever the user chose.

    Found by running it into a deep temporary folder: every merge failed with
    "Unable to open file for reading" and the self-test reported "No sample
    could be dereplicated" - an empty result with no cause given, which is
    precisely the failure it exists to prevent.
    """
    import sys as _sys

    if not _sys.platform.startswith("win"):
        return None
    headroom = MAX_WINDOWS_PATH - len(str(Path(work_dir).resolve())) - RUN_FOLDER_ALLOWANCE
    if headroom >= 0:
        return None
    return Check(
        "Self-test",
        "error",
        f"The self-test would need to write files more than "
        f"{MAX_WINDOWS_PATH} characters deep, which Windows cannot open. Its "
        f"folder would be {Path(work_dir).resolve()}, about {-headroom} "
        "characters too long.",
        fix=(
            "Choose a results folder nearer the top of the drive, such as "
            "C:/TaxaTag, and run the self-test again."
        ),
    )


def manifest(folder: Optional[Path] = None) -> Dict:
    """What the bundled data contains and what it should produce."""
    path = Path(folder or data_dir()) / MANIFEST_NAME
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def is_available(folder: Optional[Path] = None) -> bool:
    """True when the bundled data is present and readable."""
    try:
        found = manifest(folder)
    except (OSError, ValueError):
        return False
    return bool(found.get("samples"))


def _reference_library_ready(config: PipelineConfig) -> bool:
    """Whether identification can be checked as well as processing."""
    if config.blast_mode != "reference" or not config.reference_dir:
        return False
    from src.reference.library import ReferenceLibrary

    library = ReferenceLibrary(Path(config.reference_dir))
    try:
        return library.exists and bool(library.available_markers())
    except OSError:
        return False


def prepare(config: PipelineConfig, work_dir: Path, folder: Optional[Path] = None):
    """
    Lay out a self-test run: copy the reads in, and settle the settings.

    The settings are the test's own, not the user's, apart from where to
    search. A test whose result depended on the abundance floor someone last
    typed would not be a standard test, and its passing would say nothing
    about anyone else's copy.
    """
    found = manifest(folder)
    source = Path(folder or data_dir())

    work_dir = Path(work_dir)
    inputs = work_dir / "input"
    inputs.mkdir(parents=True, exist_ok=True)
    for entry in found["samples"].values():
        for member in (1, 2):
            name = f"{entry['sample']}_{member}.fastq.gz"
            shutil.copyfile(source / name, inputs / name)

    test_config = PipelineConfig(
        input_dir=inputs,
        output_base=work_dir,
        # Pinned in the manifest rather than taken from the presets, so that
        # editing a preset cannot quietly change what the test means. That the
        # two still agree is checked by tests/test_selftest.py.
        loci=[entry["locus"] for entry in found["samples"].values()],
        # The reads are already a standardised pair per sample.
        convert_sra=False,
        # Exercised deliberately: merging is a whole tool that can be broken
        # on its own, and the reads are built to overlap so that it can be.
        merge_reads=True,
        min_zotu_size=found.get("min_zotu_size", 8),
        apply_length_filter=False,
    )
    # Where to search is the one thing that is genuinely the user's, and the
    # main thing worth testing on their machine.
    test_config.blast_mode = config.blast_mode
    test_config.reference_dir = config.reference_dir
    # Carried for the same reason as the library itself: a scope is part of
    # where the run searches, and a self-test that quietly ignored it would
    # pass against references the real run would never see.
    test_config.reference_scope = config.reference_scope
    test_config.blast_db = config.blast_db
    test_config.ncbi_email = config.ncbi_email
    test_config.taxonomy_cache = config.taxonomy_cache
    test_config.threads = config.threads
    return found, test_config


def stages_for(config: PipelineConfig) -> List[str]:
    """
    Which stages the self-test can meaningfully run.

    Processing is checked always: it needs no reference data, so a user who
    has not built or downloaded a library yet can still find out whether
    their installation works. Identification is added only when there is
    something to identify against.
    """
    processing = ["standardise", "trim", "dereplicate", "denoise"]
    if _reference_library_ready(config):
        return processing + ["blast", "taxonomy"]
    return processing


# ----------------------------------------------------------------------
# Reading back what happened
# ----------------------------------------------------------------------
def _zotu_counts(run_dir: Path) -> Dict[str, int]:
    """How many ZOTUs survived, per locus."""
    counts: Dict[str, int] = {}
    base = layout.denoised_dir(run_dir)
    if not base.exists():
        return counts
    for locus_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        total = 0
        for fasta in locus_dir.glob("*_zotus.fasta"):
            with open(fasta, "r", encoding="utf-8") as handle:
                total += sum(1 for line in handle if line.startswith(">"))
        counts[locus_dir.name] = total
    return counts


def _identified_names(run_dir: Path) -> Dict[str, set]:
    """Every genus and species the run reported, per locus."""
    import csv

    found: Dict[str, set] = {}
    table = layout.identification_dir(run_dir) / layout.FINAL_SPECIES_TABLE
    if not table.exists():
        return found
    with open(table, "r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            locus = (row.get("Locus") or row.get("locus") or "").strip()
            names = found.setdefault(locus, set())
            for column in ("Genus", "Species", "genus", "species"):
                value = (row.get(column) or "").strip()
                if value:
                    names.add(value)
    return found


# ----------------------------------------------------------------------
# The checks themselves
# ----------------------------------------------------------------------
def _processing_check(found: Dict, counts: Dict[str, int]) -> Check:
    """Did every sample come through trimming and denoising intact?"""
    problems = []
    for marker, entry in sorted(found["samples"].items()):
        name = entry["locus"]["name"]
        expected = entry["expect_zotus"]
        actual = counts.get(name, 0)
        if actual != expected:
            problems.append(f"{marker} produced {actual} sequences, expected {expected}")

    if problems:
        return Check(
            "Self-test: processing",
            "error",
            "; ".join(problems) + ".",
            fix=(
                "Trimming or denoising is not working. The run folder holds the "
                "raw output of each tool under logs/, which usually names the "
                "cause on its first line."
            ),
        )
    total = sum(counts.get(e["locus"]["name"], 0) for e in found["samples"].values())
    return Check(
        "Self-test: processing",
        "ok",
        f"All {len(found['samples'])} markers trimmed, merged and denoised to "
        f"the expected {total} sequences.",
    )


def _cross_claim_check(found: Dict, counts: Dict[str, int]) -> Check:
    """
    Did each sample go to its own marker, and only its own?

    Every primer set is looked for in every sample, so this is the check that
    the primer matching still discriminates. A sample claimed by two markers
    doubles its own reads; one claimed by none disappears.
    """
    expected_loci = {e["locus"]["name"] for e in found["samples"].values()}
    unexpected = sorted(set(counts) - expected_loci)
    if unexpected:
        return Check(
            "Self-test: primer matching",
            "error",
            "Samples were claimed by primer sets they do not belong to: "
            + ", ".join(unexpected) + ".",
            fix="A primer set is matching too loosely. Check its mismatch allowance.",
        )
    return Check(
        "Self-test: primer matching",
        "ok",
        f"Each sample was assigned to exactly one marker: "
        + ", ".join(sorted(expected_loci)) + ".",
    )


def _identification_check(found: Dict, names: Dict[str, set]) -> Check:
    """Were the species that went in the ones that came out?"""
    missing, matched, at_species = [], 0, 0
    for marker, entry in sorted(found["samples"].items()):
        locus_name = entry["locus"]["name"]
        reported = names.get(locus_name, set())
        for taxon in entry["expect_taxa"]:
            if taxon["genus"] in reported:
                matched += 1
                if taxon["species"] in reported:
                    at_species += 1
            else:
                missing.append(f"{marker}: {taxon['species']}")

    expected = sum(len(e["expect_taxa"]) for e in found["samples"].values())
    if missing:
        return Check(
            "Self-test: identification",
            "error",
            f"{matched} of {expected} known species were found. Missing: "
            + "; ".join(missing) + ".",
            fix=(
                "The sequences were processed correctly but not recognised, so "
                "the reference library is the thing to look at: check it holds "
                "every marker, and that Check my setup reports it as ready."
            ),
        )
    return Check(
        "Self-test: identification",
        "ok",
        f"All {expected} known species were recovered, {at_species} of them "
        "named to species and the rest to genus.",
    )


def run(
    config: PipelineConfig,
    reporter: Optional[Reporter] = None,
    work_dir: Optional[Path] = None,
    folder: Optional[Path] = None,
) -> ValidationReport:
    """
    Run the self-test and report what it found.

    Returns the same kind of report as the pre-flight checks, so the window
    can show it in the panel it already has.
    """
    reporter = reporter or console_reporter()
    report = ValidationReport()

    if not is_available(folder):
        report.checks.append(Check(
            "Self-test data",
            "error",
            f"The bundled test data is missing from {data_dir()}.",
            fix="This ships with TaxaTag, so the installation is incomplete.",
        ))
        return report

    work_dir = Path(work_dir or (Path(config.output_base) / WORK_FOLDER))
    too_deep = room_to_work(work_dir)
    if too_deep is not None:
        report.checks.append(too_deep)
        return report

    # A previous self-test is removed rather than added to, so the result is
    # about this installation now and not about a run from three versions ago.
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)

    found, test_config = prepare(config, work_dir, folder)
    stages = stages_for(test_config)
    identifying = "blast" in stages

    reporter.info(
        f"Self-test: {len(found['samples'])} samples, "
        + ("with identification." if identifying else
           "processing only - no reference library is selected.")
    )

    from src.pipeline.runner import run_pipeline

    outcome = run_pipeline(test_config, reporter, stages=stages)
    run_dir = Path(outcome.get("run_dir") or test_config.run_dir)

    if outcome.get("status") == "error":
        report.checks.append(Check(
            "Self-test", "error",
            outcome.get("message", "The self-test run did not finish."),
            fix=f"The run folder is at {run_dir}.",
        ))
        return report

    counts = _zotu_counts(run_dir)
    report.checks.append(_cross_claim_check(found, counts))
    report.checks.append(_processing_check(found, counts))

    if identifying:
        report.checks.append(_identification_check(found, _identified_names(run_dir)))
    else:
        report.checks.append(Check(
            "Self-test: identification",
            "warning",
            "Not checked: no reference library is selected, so there was "
            "nothing to identify the sequences against.",
            fix="Choose a reference library and run the self-test again.",
        ))
    return report


def main(argv=None) -> int:
    """Run the self-test from a terminal."""
    import argparse

    parser = argparse.ArgumentParser(description="Run TaxaTag's self-test.")
    parser.add_argument("--library", type=Path, help="a reference library folder")
    parser.add_argument("--work", type=Path, help="where to put the run")
    parser.add_argument("--data", type=Path, help="a self-test data folder")
    args = parser.parse_args(argv)

    config = PipelineConfig(output_base=args.work or Path.cwd())
    if args.library:
        config.blast_mode = "reference"
        config.reference_dir = args.library

    report = run(config, work_dir=args.work, folder=args.data)
    for check in report.checks:
        print(f"[{check.status.upper():7s}] {check.name}: {check.message}")
        if check.fix:
            print(f"          -> {check.fix}")
    print(report.summary)
    return 0 if report.can_run else 1


if __name__ == "__main__":
    raise SystemExit(main())

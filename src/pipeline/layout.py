# src/pipeline/layout.py
"""
The canonical folder layout inside a run directory.

Every stage asks this module where things live rather than hard-coding folder
names, so a stage can always find its predecessor's output and a user can
predict where anything came from.

    runs/<timestamp>/
      01_reads/           FASTQ pairs, uniformly named, gzip compressed
      02_trimmed/<locus>/ primer-trimmed reads, per locus
      03_unique/<locus>/  unique sequences with abundances
      04_zotus/<locus>/   denoised ZOTUs, chimeras removed
      05_results/         database matches and the species tables
      logs/<stage>/       raw tool output, one file per sample
      reports/            a summary table for each stage
      run_manifest.json   what was run, where, and with which tools
      config_used.yaml    the exact settings for this run
      run_state.json      which stages have finished, so a run can be resumed

The folder names are kept deliberately short. Windows cannot open a file whose
full path runs past 260 characters, and these names sit between the folder a
user chose for their results and the sample's own filename, so every character
spent here is one they cannot spend on where they keep their data.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

STANDARDISED = "01_reads"
TRIMMED = "02_trimmed"
DEREPLICATED = "03_unique"
DENOISED = "04_zotus"
IDENTIFICATION = "05_results"
#: Written after a run, not by one: the Analysis tab and `python -m
#: src.analysis` put their tables here, beside the results they describe,
#: so that a run folder carries everything that was ever said about it.
ANALYSIS = "06_analysis"
LOGS = "logs"
REPORTS = "reports"

#: Human-readable stage names, keyed by the stage id used throughout the code.
STAGE_TITLES = {
    "standardise": "Stage 0 - Read in and standardise sequence files",
    "trim": "Stage 1 - Trim primers",
    "dereplicate": "Stage 2 - Collapse duplicate sequences",
    "denoise": "Stage 3 - Denoise into ZOTUs",
    "blast": "Stage 4 - Match sequences against a reference database",
    "taxonomy": "Stage 5 - Attach full taxonomy",
}

#: Execution order. The GUI and the command-line runner both follow this.
STAGE_ORDER = ["standardise", "trim", "dereplicate", "denoise", "blast", "taxonomy"]


def standardised_dir(run_dir: Path) -> Path:
    return Path(run_dir) / STANDARDISED


def trimmed_dir(run_dir: Path, locus: str | None = None) -> Path:
    base = Path(run_dir) / TRIMMED
    return base / locus if locus else base


def dereplicated_dir(run_dir: Path, locus: str | None = None) -> Path:
    base = Path(run_dir) / DEREPLICATED
    return base / locus if locus else base


def denoised_dir(run_dir: Path, locus: str | None = None) -> Path:
    base = Path(run_dir) / DENOISED
    return base / locus if locus else base


def identification_dir(run_dir: Path) -> Path:
    return Path(run_dir) / IDENTIFICATION


def analysis_dir(run_dir: Path) -> Path:
    return Path(run_dir) / ANALYSIS


def logs_dir(run_dir: Path, stage: str | None = None) -> Path:
    base = Path(run_dir) / LOGS
    return base / stage if stage else base


def reports_dir(run_dir: Path) -> Path:
    return Path(run_dir) / REPORTS


def report_csv(run_dir: Path, name: str) -> Path:
    """Path for a per-stage summary table, e.g. report_csv(run, 'trimming')."""
    return reports_dir(run_dir) / f"{name}_summary.csv"


#: A record of which stages have finished, so a run that stopped part-way can
#: be picked up rather than repeated. Written after each stage rather than at
#: the end of the run, because a run that reached its end is not one anybody
#: needs to resume.
RUN_STATE = "run_state.json"


def run_state_path(run_dir: Path) -> Path:
    return Path(run_dir) / RUN_STATE


#: The two files a user actually wants at the end of a run.
FINAL_SPECIES_TABLE = "species_composition.csv"
FINAL_TAXONOMY_TABLE = "species_composition_with_taxonomy.csv"


def ensure(*paths: Path) -> None:
    """Create every given directory, parents included."""
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


#: What a run records about itself, written before any stage runs.
MANIFEST = "run_manifest.json"


def started_at(run_dir: Path) -> float:
    """
    When a run began, as a sortable number.

    Read from what the run recorded rather than from what its folder is
    called. The two agree until somebody renames a folder - and then the
    name is the one that lies, because the manifest was written by the run
    and the name was written by a person tidying up afterwards.

    Without a manifest, the folder's own name is read as a timestamp - the
    shape every run folder is created with. That keeps ordering exactly as
    it was for a run that stopped before writing anything, and it is what
    the old name-sorting did, so nothing regresses.

    Deliberately *not* the folder's modification time, which was the first
    attempt and is wrong in the dangerous direction: a half-written folder
    has just been touched, so it would be the newest of all, and Resume
    would offer to continue the one run with nothing to continue.

    A folder that answers neither - a renamed one that also lost its
    manifest - sorts oldest. There is nothing left to date it by, and being
    overlooked costs less than being resumed in place of the real thing.
    """
    import json
    from datetime import datetime

    path = Path(run_dir) / MANIFEST
    try:
        started = json.loads(path.read_text(encoding="utf-8")).get("started")
        if started:
            return datetime.fromisoformat(str(started)).timestamp()
    except (OSError, ValueError, TypeError):
        pass

    # The timestamp prefix only, because a named run is
    # "2026-09-10_131540_pond-survey" and parsing the whole name fails on
    # the part the user chose. Reading the prefix is what makes the naming
    # feature safe without a manifest to fall back on.
    try:
        return datetime.strptime(Path(run_dir).name[:17], "%Y-%m-%d_%H%M%S").timestamp()
    except ValueError:
        return 0.0


def runs_by_recency(output_base: Path) -> List[Path]:
    """
    Every run under `output_base`, newest first.

    One implementation, because there were two and they disagreed about
    direction: `find_latest_run` sorted names ascending and took the last,
    while the run-time estimate sorted descending and took the first. Both
    were right about names and both would have been wrong the moment a
    folder was renamed - "Pond survey" sorts after every timestamp, so it
    would have become permanently "the most recent" and been resumed
    forever.
    """
    runs_root = Path(output_base) / "runs"
    try:
        folders = [p for p in runs_root.iterdir() if p.is_dir()]
    except OSError:
        return []
    return sorted(folders, key=started_at, reverse=True)

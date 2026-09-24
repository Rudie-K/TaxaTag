# src/analysis/workbench.py
"""
What the Analysis tab does, without the window: which analyses it offers,
whether each can run on the chosen run, running one, and saving it.

Kept apart from `src/gui/analysis.py` so that every rule here is tested
without Qt, and so the terminal and the window run the same code (the
analyses themselves are `diversity`, `sites` and `effort`; this only
arranges them).

**Run** writes an analysis's files into a private working folder, using the
same `write_*` functions the terminal does, and returns the tables they
wrote. The window shows those tables and that notes file. **Save** copies
the folder to where the user chose, with `analysis_settings.json` beside
it, so what is saved is exactly what was shown and nothing is computed
twice (decision 0039).

A result lives with its inputs: one run's result is saved inside that run,
`<run>/06_analysis/<analysis>_<date>_<time>/`.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from src.analysis import diversity, effort, readiness, sites
from src.pipeline import layout

#: The status words `readiness.status` gives, plus the reasons behind them.
AVAILABLE = "available"


@dataclass
class Context:
    """Everything an analysis is run on, as chosen in the tab's top bar and options row."""

    run_dir: Path
    locus: str = ""
    sheet: Optional[sites.SampleSheet] = None
    basis: str = diversity.MIXED
    keep_contaminants: bool = False
    seed: int = effort.SEED


@dataclass
class Table:
    """One table of a result, as the window shows it."""

    label: str
    columns: List[str]
    rows: List[Dict[str, object]]


@dataclass
class Outcome:
    """A run analysis: its tables, notes and findings, and the folder its files wait in until saved."""

    spec: "Spec"
    context: Context
    tables: List[Table]
    notes: str
    findings: List[readiness.Finding]
    staged: Path
    ran_at: datetime.datetime = field(default_factory=datetime.datetime.now)
    saved_to: Optional[Path] = None


@dataclass(frozen=True)
class Spec:
    """
    One entry in the tab's list. `key` names its save folder; `title` and
    `subtitle` are what the list shows, in conversational English or the
    field's terms (`writing-the-interface.md` rule 10).
    """

    key: str
    title: str
    subtitle: str
    group: str
    affects: str
    tooltip: str
    needs_sheet: bool
    writer: Callable[[Context, Path], Dict[str, object]]
    shown: Tuple[Tuple[str, str], ...]          # (label, table name) in the order offered
    chart: bool = False


# ---------------------------------------------------------------- the analyses


def _write_diversity(context: Context, folder: Path):
    return diversity.write_diversity(context.run_dir, context.basis, context.keep_contaminants, out_dir=folder)


def _write_sites(context: Context, folder: Path):
    return sites.write_sites(context.run_dir, context.sheet, context.basis, context.keep_contaminants,
                             out_dir=folder)


def _write_effort(context: Context, folder: Path):
    return effort.write_effort(context.run_dir, context.sheet, context.basis, context.keep_contaminants,
                               out_dir=folder, seed=context.seed)


DIVERSITY = "Diversity"

SPECS: Tuple[Spec, ...] = (
    Spec("alpha_diversity", "Alpha diversity", "richness, Shannon, Simpson", DIVERSITY, readiness.DIVERSITY,
         "How many taxa each sample holds, and how evenly its reads are shared among them.",
         False, _write_diversity, (("Per sample", "summary"),)),
    Spec("beta_diversity", "Beta diversity", "Sørensen, Jaccard", DIVERSITY, readiness.BETA,
         "How different each pair of samples is, split into taxa replaced and taxa missing.",
         False, _write_diversity, (("Sample pairs", "beta"), ("Presence", "presence"), ("Reads", "reads"))),
    Spec("sites", "Sites", "replicates pooled by site", DIVERSITY, readiness.SITES,
         "Replicates pooled into the sites of your sample sheet, with how often each taxon was found.",
         True, _write_sites, (("Per site", "sites"), ("Occurrence", "occurrence"), ("Site pairs", "beta"))),
    Spec("species_accumulation", "Species accumulation", "sampling effort, Chao2", DIVERSITY, readiness.EFFORT,
         "Whether sampling was enough: taxa expected from fewer or more samples, and an estimate of those missed.",
         False, _write_effort,
         (("Summary", "summary"), ("Curve", "curve"), ("Sites at equal effort", "sites")), chart=True),
)

BY_KEY = {spec.key: spec for spec in SPECS}

NEEDS_A_SHEET = "Choose a sample sheet in the bar above: sites are pooled from it."


# ---------------------------------------------------------------- what can run


def loci(run_dir: Path) -> List[str]:
    """The markers a run's species table holds, in order."""
    return sorted({row.get("Locus", "") for row in diversity.read_table(run_dir) if row.get("Locus")})


def statuses(context: Context) -> Dict[str, Tuple[str, List[str]]]:
    """
    For each analysis: `blocked`, `warning` or `available` on this run and
    marker, with the reasons - the exact texts of `readiness`'s conditions
    (decision 0034), or the missing sample sheet.
    """
    findings = readiness.check(context.run_dir, context.sheet, context.basis, context.keep_contaminants)
    result = {}
    for spec in SPECS:
        if spec.needs_sheet and context.sheet is None:
            result[spec.key] = (readiness.BLOCKED, [NEEDS_A_SHEET])
            continue
        state = readiness.state(findings, spec.affects, context.locus)
        relevant = [f for f in findings if f.condition.affects in (spec.affects, readiness.NAMES)
                    and (not f.locus or not context.locus or f.locus == context.locus)]
        blocked = [f.text for f in relevant if f.tier == readiness.BLOCKED and not f.subject]
        warned = [f.text for f in state["warnings"]]
        partly = [f.text for f in relevant if f.tier == readiness.BLOCKED and f.subject]
        status = readiness.status(findings, spec.affects, context.locus)
        result[spec.key] = (status, _unique(blocked + partly + warned))
    return result


def _unique(texts: List[str]) -> List[str]:
    return list(dict.fromkeys(texts))


# ---------------------------------------------------------------- running and saving


def run(spec: Spec, context: Context) -> Outcome:
    """Compute one analysis into a private working folder, and return what it wrote."""
    staged = Path(tempfile.mkdtemp(prefix=f"taxatag-{spec.key}-"))
    written = spec.writer(context, staged)
    raw = written["tables"]
    tables = []
    for label, name in spec.shown:
        rows = [row for row in raw.get(name, []) if not context.locus or row.get("Locus") == context.locus]
        if name not in raw or (spec.key == "species_accumulation" and name == "sites" and context.sheet is None):
            continue
        tables.append(Table(label, _columns(rows), rows))
    notes = "\n\n".join(path.read_text(encoding="utf-8") for path in sorted(staged.glob("*notes*.txt")))
    return Outcome(spec, context, tables, notes, written["findings"], staged)


def _columns(rows: List[Dict[str, object]]) -> List[str]:
    columns: List[str] = []
    for row in rows:
        for column in row:
            if column not in columns:
                columns.append(column)
    return columns


def default_folder(outcome: Outcome) -> Path:
    """Where Save offers to put a result: inside its run, named for the analysis and the time it ran."""
    stamp = outcome.ran_at.strftime("%Y-%m-%d_%H%M")
    return layout.analysis_dir(outcome.context.run_dir) / f"{outcome.spec.key}_{stamp}"


def species_table_checksum(run_dir: Path) -> str:
    """SHA-256 of the species table the analysis read, so a saved result can tell if its run changed."""
    table = layout.identification_dir(run_dir)
    for name in (layout.FINAL_TAXONOMY_TABLE, layout.FINAL_SPECIES_TABLE):
        if (table / name).exists():
            return hashlib.sha256((table / name).read_bytes()).hexdigest()
    return ""


def save(outcome: Outcome, destination: Path) -> Path:
    """
    Copy a result's files to `destination`, which must not exist yet, and
    record how it was made in `analysis_settings.json` beside them.
    """
    from src.utils import platform as platform_utils
    from src.version import __version__

    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"{destination} already exists; a saved result is never overwritten")
    shutil.copytree(outcome.staged, destination)
    context = outcome.context
    settings = {
        "analysis": outcome.spec.title,
        "ran_at": outcome.ran_at.isoformat(timespec="seconds"),
        "taxatag": __version__,
        "built": platform_utils.build_provenance(),
        "inputs": [{
            "run": str(context.run_dir),
            "run_name": Path(context.run_dir).name,
            "species_table_sha256": species_table_checksum(context.run_dir),
        }],
        "marker_shown": context.locus,
        "note": "The files cover every marker in the run; the window showed one.",
        "sample_sheet": None if context.sheet is None else {
            "path": str(context.sheet.path),
            "sample_column": context.sheet.sample_column,
            "site_column": context.sheet.site_column,
        },
        "rank": context.basis,
        "keep_contaminants": context.keep_contaminants,
        "seed": context.seed if outcome.spec.writer is _write_effort else None,
        "files": sorted(p.name for p in destination.iterdir()),
    }
    (destination / "analysis_settings.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
    outcome.saved_to = destination
    return destination


def discard(outcome: Outcome) -> None:
    """Remove an unsaved result's working folder."""
    shutil.rmtree(outcome.staged, ignore_errors=True)


# ---------------------------------------------------------------- the chart


@dataclass
class Curve:
    """The accumulation curve for one marker's survey, as the chart draws it."""

    unit: str
    points: List[Tuple[int, float, float, float, str]]   # units, richness, lower, upper, method
    observed_at: int


def accumulation_curve(outcome: Outcome) -> Optional[Curve]:
    """The survey-level curve of a species accumulation result, for the shown marker."""
    if not outcome.spec.chart:
        return None
    curve = next((t for t in outcome.tables if t.label == "Curve"), None)
    if curve is None:
        return None
    rows = [r for r in curve.rows if r.get("Scope") == effort.SURVEY]
    if not rows:
        return None
    points = [(int(r["Units"]), float(r["Richness"]), float(r["Richness_Lower"]), float(r["Richness_Upper"]),
               str(r["Method"])) for r in rows]
    observed = next((p[0] for p in points if p[4] == effort.OBSERVED), points[-1][0])
    return Curve(str(rows[0].get("Unit", "unit")), points, observed)

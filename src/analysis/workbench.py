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

from src.analysis import adjudication, candidates, coverage, diversity, effort, metrics, readiness, sites
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
    library: object = None                                   # the run's own reference library
    species_list: Optional[adjudication.SpeciesList] = None
    review_sheet: Optional[Path] = None                      # a filled review sheet, for accuracy


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
    affects: Optional[str]                      # readiness's scope; None for what reads no run's names
    tooltip: str
    needs_sheet: bool
    writer: Callable[[Context, Path], Dict[str, object]]
    shown: Tuple[Tuple[str, str], ...]          # (label, table name) in the order offered
    chart: bool = False
    needs_list: bool = False
    needs_review: bool = False
    needs_library: bool = False
    uses_sheet: bool = False                    # a sample sheet helps, but is not required
    uses_list: bool = False                     # a species list helps, but is not required

    @property
    def shows_sheet(self) -> bool:
        return self.needs_sheet or self.uses_sheet

    @property
    def shows_list(self) -> bool:
        return self.needs_list or self.uses_list
    options: Tuple[str, ...] = ("rank", "contaminants")   # which of the options row it uses


# ---------------------------------------------------------------- the analyses


def _write_diversity(context: Context, folder: Path):
    return diversity.write_diversity(context.run_dir, context.basis, context.keep_contaminants, out_dir=folder)


def _write_sites(context: Context, folder: Path):
    return sites.write_sites(context.run_dir, context.sheet, context.basis, context.keep_contaminants,
                             out_dir=folder)


def _write_effort(context: Context, folder: Path):
    return effort.write_effort(context.run_dir, context.sheet, context.basis, context.keep_contaminants,
                               out_dir=folder, seed=context.seed)


def _write_possible_species(context: Context, folder: Path):
    path = candidates.write_candidates(context.run_dir, context.library, out_dir=folder)
    return {"written": [path], "tables": {"candidates": candidates.read_table(path)}, "findings": []}


def _write_review_sheet(context: Context, folder: Path):
    path = adjudication.write_sheet(context.run_dir, context.library, context.species_list, out_dir=folder)
    return {"written": [path], "tables": {"sheet": adjudication.read_sheet(path)}, "findings": []}


def _write_accuracy(context: Context, folder: Path):
    # The run's possible species, computed afresh: a saved result lives in
    # a dated folder of its own, not where the terminal would look.
    possible = candidates.candidate_rows(context.run_dir, context.library) if context.library else []
    result = metrics.write_audit(context.run_dir, Path(context.review_sheet), folder, possible)
    tables = {name: _read_csv(path) for name, path in result["written"].items()}
    (folder / "accuracy-notes.txt").write_text(
        f"Rows on the sheet: {result['rows']}; not yet judged: {result['unjudged']}.\n"
        f"Confident species calls: {result['confident']}; of those, wrong: "
        f"{sum(result['confident_but_wrong'].values())} "
        f"({result['confident_but_wrong'][metrics.CAUSE_MISASSIGNED]} misassigned, "
        f"{result['confident_but_wrong'][metrics.CAUSE_FOREIGN]} foreign DNA).\n"
        "Rows not yet judged are left out of every number.\n",
        encoding="utf-8")
    return {"written": list(result["written"].values()), "tables": tables, "findings": []}


def _write_gaps(context: Context, folder: Path):
    markers = sorted(set(marker_by_locus(context.run_dir).values()))
    path = folder / "coverage.csv"
    rows = coverage.write_coverage(context.library, context.species_list, markers, path)
    return {"written": [path], "tables": {"coverage": rows}, "findings": []}


def _read_csv(path: Path) -> List[Dict[str, str]]:
    import csv

    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


DIVERSITY = "Diversity"
IDENTIFICATION = "Species identification"
LIBRARY = "Reference library"

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
         (("Summary", "summary"), ("Curve", "curve"), ("Sites at equal effort", "sites")), chart=True,
         options=("rank", "contaminants", "seed"), uses_sheet=True),
    Spec("possible_species", "Possible species", "every close match, ranked", IDENTIFICATION, readiness.NAMES,
         "For each sequence, the references it matched best, ranked, with how many references each species "
         "has - where a call could have gone another way.",
         False, _write_possible_species, (("Candidates", "candidates"),), needs_library=True, options=()),
    Spec("review_sheet", "Review sheet", "each call with its evidence", IDENTIFICATION, readiness.NAMES,
         "One row per call with its evidence beside it, and empty Outcome and Detection columns for you to "
         "fill. Save it, fill it in a spreadsheet, then choose it for identification accuracy. A species list "
         "adds whether each name is on it, and its habitat.",
         False, _write_review_sheet, (("Calls", "sheet"),), needs_library=True, options=(), uses_list=True),
    Spec("identification_accuracy", "Identification accuracy", "precision and accuracy by rank", IDENTIFICATION,
         readiness.NAMES,
         "Precision and accuracy at each rank from a filled review sheet, as Bourret et al. (2023) define "
         "them, and the confident calls your review found wrong.",
         False, _write_accuracy,
         (("By rank", "metrics"), ("By sample", "by_sample"), ("What stopped short", "breakdown"),
          ("Top matches", "top_k"), ("Confident but wrong", "confident_but_wrong"), ("Could be either", "could_be")),
         needs_review=True, needs_library=True, options=()),
    Spec("reference_gaps", "Reference gaps", "species on your list with no reference", LIBRARY, None,
         "Which species on your list the library holds a reference for on each marker, only a relative of, or "
         "nothing. A species with no reference cannot be named, however good the sequencing.",
         False, _write_gaps, (("By species", "coverage"),), needs_list=True, needs_library=True, options=()),
)

BY_KEY = {spec.key: spec for spec in SPECS}

NEEDS_A_SHEET = "Choose a sample sheet above: it says which site each sample came from."
NEEDS_A_LIST = "Choose a species list above: it says which species to look for."
NEEDS_A_FILLED_SHEET = ("Choose a filled review sheet above: save the Review sheet, fill in its Outcome and "
                        "Detection columns, then choose it here.")

#: What each input is, said once, under the analysis that uses it (writing-the-interface.md rule 4).
ABOUT_THE_SHEET = ("A sample sheet is a file you make: one row per sample, naming the site it came from - "
                   "for example two columns, Sample and Site. It is not the run's results table.")
ABOUT_THE_LIST = ("A species list is the species that could be there - for example a regional checklist - "
                  "one per row, in a column called ScientificName.")

#: The columns only a TaxaTag results table has; a file carrying them is not a sheet or a list.
RESULTS_COLUMNS = {"Scientific_Name", "ZOTU"}
NOT_A_SHEET = ("That file is a run's results table, not a sample sheet. " + ABOUT_THE_SHEET.split(": ", 1)[0]
               + ": one row per sample, naming the site it came from.")
NOT_A_LIST = ("That file is a run's results table, not a species list. A species list is the species that "
              "could be there, one per row, in a column called ScientificName.")


def is_results_table(columns) -> bool:
    """Whether a CSV's columns are a TaxaTag results table's, chosen where a sheet or list was wanted."""
    return bool(RESULTS_COLUMNS & set(columns))
NEEDS_THE_LIBRARY = "The reference library this run was searched against could not be found on this computer."


# ---------------------------------------------------------------- what can run


def loci(run_dir: Path) -> List[str]:
    """The markers a run's species table holds, in order."""
    return sorted({row.get("Locus", "") for row in diversity.read_table(run_dir) if row.get("Locus")})


def unfound_loci(run_dir: Path) -> List[str]:
    """
    Primer sets the run looked for and found in no sample, from its trimming
    summary. The marker menu lists only what has results, so without this a
    primer set that was switched on and matched nothing simply vanished: the
    1.1.0 test run of a folder named "MiFish_test" offered only MarVer3_16S,
    because the folder held the 16S samples (24 September 2026).
    """
    import csv

    summary = layout.report_csv(run_dir, "trimming")
    try:
        with open(summary, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return []
    looked_for = {row.get("Locus", "") for row in rows if row.get("Locus")}
    found = {row.get("Locus", "") for row in rows if row.get("Status") == "OK"}
    return sorted((looked_for - found) - set(loci(run_dir)))


def marker_by_locus(run_dir: Path) -> Dict[str, str]:
    """Each primer set in the run, to the marker gene it amplifies."""
    return {row["Locus"]: row.get("Marker", "") for row in diversity.read_table(run_dir) if row.get("Locus")}


def library_for(run_dir: Path):
    """
    The reference library a run was searched against, from its own record
    of its settings, as the terminal finds it; None if it is not on this
    computer any more.
    """
    import yaml

    from src.reference.library import ReferenceLibrary

    used = Path(run_dir) / "config_used.yaml"
    try:
        settings = yaml.safe_load(used.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    folder = (settings.get("paths") or {}).get("reference_dir") or ""
    library = ReferenceLibrary(Path(folder)) if folder else None
    return library if library is not None and library.exists else None


def statuses(context: Context) -> Dict[str, Tuple[str, List[str]]]:
    """
    For each analysis: `blocked`, `warning` or `available` on this run and
    marker, with the reasons - the exact texts of `readiness`'s conditions
    (decision 0034), or the missing sample sheet.
    """
    findings = readiness.check(context.run_dir, context.sheet, context.basis, context.keep_contaminants)
    result = {}
    for spec in SPECS:
        missing = ([NEEDS_A_SHEET] if spec.needs_sheet and context.sheet is None else []) \
            + ([NEEDS_THE_LIBRARY] if spec.needs_library and context.library is None else []) \
            + ([NEEDS_A_LIST] if spec.needs_list and context.species_list is None else []) \
            + ([NEEDS_A_FILLED_SHEET] if spec.needs_review and not context.review_sheet else [])
        if missing:
            result[spec.key] = (readiness.BLOCKED, missing)
            continue
        if spec.affects is None:
            result[spec.key] = (AVAILABLE, [])
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
    marker = marker_by_locus(context.run_dir).get(context.locus, "")
    for label, name in spec.shown:
        if name not in raw or (spec.key == "species_accumulation" and name == "sites" and context.sheet is None):
            continue
        rows = _for_the_marker(raw.get(name, []), context.locus, marker)
        tables.append(Table(label, _columns(rows), rows))
    note_files = sorted(set(staged.glob("*notes*.txt")) | set(staged.glob("*sources*.txt")))
    notes = "\n\n".join(path.read_text(encoding="utf-8") for path in note_files)
    return Outcome(spec, context, tables, notes, written["findings"], staged)


def _for_the_marker(rows: List[Dict[str, object]], locus: str, marker: str) -> List[Dict[str, object]]:
    """
    One marker at a time (decision 0039): by primer set where a table names
    one, by marker gene where it names only that, and whole where it names
    neither - an accuracy table is over the whole review sheet.
    """
    if not locus or not rows:
        return list(rows)
    if "Locus" in rows[0]:
        return [row for row in rows if row.get("Locus") == locus]
    if "Marker" in rows[0] and marker:
        return [row for row in rows if row.get("Marker") == marker]
    return list(rows)


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
        "rank": context.basis if "rank" in outcome.spec.options else None,
        "keep_contaminants": context.keep_contaminants if "contaminants" in outcome.spec.options else None,
        "seed": context.seed if "seed" in outcome.spec.options else None,
        "library": str(getattr(context.library, "root", "")) or None,
        "species_list": getattr(context.species_list, "source", None),
        "review_sheet": str(context.review_sheet) if context.review_sheet else None,
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

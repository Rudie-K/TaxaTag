# src/analysis/metrics.py
"""
The numbers from a filled adjudication sheet, in the literature's terms.

Once a person has answered the sheet's two questions for every row - how
the name did (`Outcome`) and whether the DNA counts (`Detection`) - this
turns the answers into the figures a paper reports, defined as Bourret
et al. (2023) define them after Bokulich et al. (2018), so that a number
from TaxaTag can be set beside a published one without translation.

**Per rank**, because a genus the marker cannot split is a true positive
at genus and a false negative at species, and a single "accuracy" would
punish the pipeline for being honest (decision 0026). At each of Species,
Genus and Family a judged row is one of:

    TP   the call carries the right name at that rank
    FP   the call carries a wrong name at that rank (Reassigned, and the
         right species is in a different genus / family)
    FN   the call stopped above that rank - whether because the evidence
         could take it lower (Under-classified) or nothing could (Accepted
         at genus or family); the breakdown table keeps the two apart

Unresolved rows are counted and left out of the three; rows with no
outcome are "unjudged" and reported, because a sheet half filled is not a
result. Precision is TP / (TP + FP), accuracy TP / (TP + FP + FN), each
in two scopes: every judged row, and only the *genuine* detections -
the survey's own view. A perfectly named pike that is out of scope is a
TP for the pipeline and absent from the survey; both numbers are wanted.

**Top-k accuracy** asks whether the species the adjudicator settled on
was among the first k candidates by bitscore, for the rows where a species
was settled - the measure of how far down a candidate list a reader
would have had to look.

**Confident-but-wrong** is the number the Sussex Audit was built to
measure: a call that carried no ambiguity flag and was nevertheless
wrong - either the wrong name (a misassignment, the reference-database
result) or the right name for DNA that was never in the water (foreign
DNA, a laboratory result). The two are listed with the library's
coverage of the winner beside them and are never added together. What
counts as "confident" is `is_confident()`, and is the user's definition.

Nothing here reads a run's reads or hits; the sheet and, when present,
the candidates file are the whole input, so the same numbers come from a
sheet filled on a cluster or copied to a project folder.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from src.analysis import adjudication as adjudication_module
from src.pipeline import layout

#: The ranks a paper reports at, top down. Order and class are not reported:
#: nobody publishes an order-rank accuracy, and a call that stopped there is
#: a false negative at every rank below it.
REPORT_RANKS = ("Species", "Genus", "Family")
RANK_DEPTH = {"Species": 0, "Genus": 1, "Family": 2, "Order": 3, "Class": 4, "Phylum": 5, "Kingdom": 6}

SCOPE_ALL = "all"            # every judged row
SCOPE_GENUINE = "genuine"    # only rows whose Detection is genuine

TOP_K = (1, 3, 5, 10)

METRIC_COLUMNS = ["Scope", "Rank", "Judged", "TP", "FP", "FN", "Unresolved", "Precision", "Accuracy"]
CONFIDENT_COLUMNS = ["Sample", "ZOTU", "Call", "Cause", "Outcome", "Detection", "Outcome_Species",
                     "Identity_Percent", "References_Matched", "Species_References", "Genus_References",
                     "Genus_Species", "Reads", "Note"]
TOP_K_COLUMNS = ["K", "Rows", "Hits", "Fraction"]
BREAKDOWN_COLUMNS = ["Call_Rank", "Outcome", "Rows"]
BY_SAMPLE_COLUMNS = ["Sample", "Rank", "Judged", "TP", "FP", "FN", "Unresolved"]

CAUSE_MISASSIGNED = "misassignment"   # Reassigned: the wrong name - the reference-database result
CAUSE_FOREIGN = "foreign DNA"         # spurious: the right name for DNA that was never there


# ---------------------------------------------------------------- one row, one rank


def _depth(rank: str) -> int:
    return RANK_DEPTH.get(rank, 99)


def _genus_of(name: str) -> str:
    return name.split(" ")[0] if name else ""


def _families(candidates: Iterable[Dict[str, str]]) -> Dict[str, str]:
    """Species -> family, from wherever the candidates file names one."""
    families: Dict[str, str] = {}
    for row in candidates:
        if row.get("Candidate") and row.get("Candidate_Family") and " " in row["Candidate"]:
            families.setdefault(row["Candidate"], row["Candidate_Family"])
    return families


def classify(row: Dict[str, str], rank: str, families: Dict[str, str]) -> str:
    """
    TP, FP, FN, "unresolved" or "unjudged" for one row at one reporting rank.

    A Reassigned call is wrong at species by definition; at genus it is
    right if the settled species shares the call's genus, and at family if
    the two are filed in the same family - which needs the candidates file
    to say what the families are. Without it, a different genus counts as
    wrong at family too, which errs on the side of the pipeline looking
    worse, never better.
    """
    outcome = row.get("Outcome", "")
    if not outcome:
        return "unjudged"
    if outcome == "Unresolved":
        return "unresolved"
    called_at = _depth(row.get("Call_Rank", ""))
    wanted = _depth(rank)
    if outcome in ("Accepted", "Under-classified"):
        return "TP" if called_at <= wanted else "FN"
    if outcome == "Reassigned":
        call, right = row.get("Call", ""), row.get("Outcome_Species", "")
        if rank == "Species":
            return "FP"
        if rank == "Genus":
            return "TP" if _genus_of(call) and _genus_of(call) == _genus_of(right) else "FP"
        same_genus = _genus_of(call) and _genus_of(call) == _genus_of(right)
        same_family = families.get(call) and families.get(call) == families.get(right)
        return "TP" if (same_genus or same_family) else "FP"
    return "unjudged"


def is_confident(row: Dict[str, str]) -> bool:
    """
    Whether a call was made with nothing on the sheet to make a reader
    doubt it - the calls whose being wrong is the project's phenomenon.

    TODO(human): decide what "confident" means for the write-up and return
    it here. The row is a dict of the sheet's columns as strings: `Flag`
    ("" when the tied references named one species; "F2 ..." etc. when
    they did not), `Call_Rank`, `Identity_Percent`, `References_Matched`,
    `Agreement_Percent`, `Species_References`. See the note in the
    conversation for the two candidate definitions.
    """
    raise NotImplementedError("is_confident() is the project's definition; see TODO(human)")


# ---------------------------------------------------------------- the tables


def _tally(rows: List[Dict[str, str]], rank: str, families: Dict[str, str]) -> Dict[str, int]:
    counts = {"TP": 0, "FP": 0, "FN": 0, "unresolved": 0, "unjudged": 0}
    for row in rows:
        counts[classify(row, rank, families)] += 1
    return counts


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.4f}" if denominator else ""


def metrics_table(rows: List[Dict[str, str]], families: Dict[str, str]) -> List[Dict[str, str]]:
    """Precision and accuracy per rank, in both scopes."""
    table = []
    for scope in (SCOPE_ALL, SCOPE_GENUINE):
        chosen = [r for r in rows if scope == SCOPE_ALL or r.get("Detection") == "genuine"]
        for rank in REPORT_RANKS:
            c = _tally(chosen, rank, families)
            judged = c["TP"] + c["FP"] + c["FN"] + c["unresolved"]
            table.append({
                "Scope": scope, "Rank": rank, "Judged": str(judged),
                "TP": str(c["TP"]), "FP": str(c["FP"]), "FN": str(c["FN"]), "Unresolved": str(c["unresolved"]),
                "Precision": _ratio(c["TP"], c["TP"] + c["FP"]),
                "Accuracy": _ratio(c["TP"], c["TP"] + c["FP"] + c["FN"]),
            })
    return table


def by_sample_table(rows: List[Dict[str, str]], families: Dict[str, str]) -> List[Dict[str, str]]:
    """The same counts per sample, every judged row, for the statistics done outside."""
    table = []
    for sample in sorted({r["Sample"] for r in rows}):
        chosen = [r for r in rows if r["Sample"] == sample]
        for rank in REPORT_RANKS:
            c = _tally(chosen, rank, families)
            table.append({"Sample": sample, "Rank": rank,
                          "Judged": str(c["TP"] + c["FP"] + c["FN"] + c["unresolved"]),
                          "TP": str(c["TP"]), "FP": str(c["FP"]), "FN": str(c["FN"]), "Unresolved": str(c["unresolved"])})
    return table


def breakdown_table(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Rows by the rank the call stopped at and what the adjudicator made of
    it. This is where a false negative at species splits into the two
    things it can be: a call the evidence could take lower
    (Under-classified) and one nothing could (Accepted above species).
    """
    counts: Dict[Tuple[str, str], int] = {}
    for row in rows:
        key = (row.get("Call_Rank", ""), row.get("Outcome", "") or "(unjudged)")
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (_depth(item[0][0]), item[0][1]))
    return [{"Call_Rank": rank, "Outcome": outcome, "Rows": str(n)} for (rank, outcome), n in ordered]


def settled_species(row: Dict[str, str]) -> str:
    """The species the adjudicator settled on, or "" when none was."""
    outcome = row.get("Outcome", "")
    if outcome == "Accepted" and row.get("Call_Rank") == "Species":
        return row.get("Call", "")
    if outcome in ("Under-classified", "Reassigned"):
        return row.get("Outcome_Species", "")
    return ""


def top_k_table(rows: List[Dict[str, str]], candidates: List[Dict[str, str]], ks: Tuple[int, ...] = TOP_K) -> List[Dict[str, str]]:
    """
    Of the rows with a settled species, how many had it within the first k
    candidate species by bitscore. A species-rank call the adjudicator
    accepted is the first candidate by construction.
    """
    by_call: Dict[Tuple[str, str], List[str]] = {}
    for c in candidates:
        key = (c["Sample"], c["ZOTU"])
        names = by_call.setdefault(key, [])
        if c.get("Candidate") and " " in c["Candidate"] and c["Candidate"] not in names:
            names.append(c["Candidate"])
    positions: List[Optional[int]] = []
    for row in rows:
        species = settled_species(row)
        if not species:
            continue
        if row.get("Outcome") == "Accepted":
            positions.append(1)
            continue
        names = by_call.get((row["Sample"], row["ZOTU"]), [])
        positions.append(names.index(species) + 1 if species in names else None)
    table = []
    for k in ks:
        hits = sum(1 for p in positions if p is not None and p <= k)
        table.append({"K": str(k), "Rows": str(len(positions)), "Hits": str(hits), "Fraction": _ratio(hits, len(positions))})
    return table


def confident_but_wrong(rows: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], int]:
    """
    The confident calls that were wrong, each with its cause, and the
    number of confident calls they are out of. Misassignment and foreign
    DNA are listed together and must be reported apart.
    """
    confident = [r for r in rows if r.get("Outcome") and is_confident(r)]
    wrong = []
    for row in confident:
        cause = ""
        if row.get("Outcome") == "Reassigned":
            cause = CAUSE_MISASSIGNED
        elif row.get("Detection") == "spurious":
            cause = CAUSE_FOREIGN
        if cause:
            wrong.append({**{c: row.get(c, "") for c in CONFIDENT_COLUMNS if c != "Cause"}, "Cause": cause})
    return wrong, len(confident)


# ---------------------------------------------------------------- files


def _write(path: Path, columns: List[str], rows: List[Dict[str, str]]) -> Path:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def read_candidates(run_dir: Path) -> List[Dict[str, str]]:
    path = layout.analysis_dir(run_dir) / "candidates.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_audit(run_dir: Path, sheet: Path, out_dir: Optional[Path] = None) -> Dict[str, object]:
    """
    Read a filled sheet, write the four tables beside it (or into
    `out_dir`), and return what was written and what was found. The
    confident-but-wrong table is written only once `is_confident()` has
    been given its definition; until then the result says so.
    """
    rows = adjudication_module.read_sheet(sheet)
    candidates = read_candidates(run_dir)
    families = _families(candidates)
    folder = out_dir or layout.analysis_dir(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    table = metrics_table(rows, families)
    written = {
        "metrics": _write(folder / "audit.csv", METRIC_COLUMNS, table),
        "by_sample": _write(folder / "audit-by-sample.csv", BY_SAMPLE_COLUMNS, by_sample_table(rows, families)),
        "breakdown": _write(folder / "audit-breakdown.csv", BREAKDOWN_COLUMNS, breakdown_table(rows)),
        "top_k": _write(folder / "audit-top-k.csv", TOP_K_COLUMNS, top_k_table(rows, candidates)),
    }
    result: Dict[str, object] = {
        "written": written, "table": table, "rows": len(rows),
        "unjudged": sum(1 for r in rows if not r.get("Outcome")),
        "sources": sheet,
    }
    try:
        wrong, confident = confident_but_wrong(rows)
    except NotImplementedError as pending:
        result["confident_pending"] = str(pending)
    else:
        written["confident_but_wrong"] = _write(folder / "audit-confident-but-wrong.csv", CONFIDENT_COLUMNS, wrong)
        result["confident"] = confident
        result["confident_but_wrong"] = {
            CAUSE_MISASSIGNED: sum(1 for w in wrong if w["Cause"] == CAUSE_MISASSIGNED),
            CAUSE_FOREIGN: sum(1 for w in wrong if w["Cause"] == CAUSE_FOREIGN),
        }
    (folder / "audit-sources.txt").write_text(
        "Where the audit numbers came from\n\n"
        f"sheet:       {sheet}\n"
        f"candidates:  {layout.analysis_dir(run_dir) / 'candidates.csv'} ({'read' if candidates else 'absent - no top-k, families unknown'})\n"
        "definitions: Bourret et al. 2023 after Bokulich et al. 2018; per rank; two scopes (all judged rows, genuine detections)\n"
        "confident:   " + ("is_confident() in src/analysis/metrics.py" if "confident" in result else "not yet defined") + "\n",
        encoding="utf-8",
    )
    return result

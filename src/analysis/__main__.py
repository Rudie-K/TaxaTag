# src/analysis/__main__.py
"""
The analysis tools from the terminal, so that a run made on a cluster can
be described there too.

    python -m src.analysis candidates <run folder> [--library <folder>] [--top 10]
    python -m src.analysis adjudication <run folder> [--species-list <csv>] [--library <folder>]
    python -m src.analysis metrics <run folder> [--sheet <filled csv>] [--out <folder>]

`<run folder>` is one of the dated folders under `runs/`. The library is
read from the run's own `config_used.yaml` unless given, so the candidates
are named by the library that named the call. `metrics` needs no library:
it reads the filled sheet - the run's own, or a copy filled elsewhere -
and the candidates file if it is there.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from src.analysis import adjudication as adjudication_module
from src.analysis import candidates as candidates_module
from src.analysis import metrics as metrics_module
from src.pipeline import layout
from src.reference.library import ReferenceLibrary


def _library_for(run_dir: Path, given: Path | None) -> ReferenceLibrary | None:
    if given:
        return ReferenceLibrary(given)
    used = run_dir / "config_used.yaml"
    if used.exists():
        config = yaml.safe_load(used.read_text(encoding="utf-8")) or {}
        folder = (config.get("paths") or {}).get("reference_dir") or ""
        if folder:
            return ReferenceLibrary(Path(folder))
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.analysis", description=__doc__.strip().splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    cand = commands.add_parser("candidates", help="every name each ZOTU could have had, ranked and flagged")
    cand.add_argument("run_dir", type=Path, help="a finished run folder (runs/<date>)")
    cand.add_argument("--library", type=Path, help="the TaxaTag reference library the run used")
    cand.add_argument("--top", type=int, default=candidates_module.TOP_N, help="candidates per ZOTU")
    adj = commands.add_parser("adjudication", help="one row per call with the evidence beside it, and an empty Outcome column")
    adj.add_argument("run_dir", type=Path, help="a finished run folder (runs/<date>)")
    adj.add_argument("--library", type=Path, help="the TaxaTag reference library the run used")
    adj.add_argument("--species-list", type=Path, help="a CSV of species for the region: ScientificName, optional Synonyms and Habitat")
    met = commands.add_parser("metrics", help="precision, accuracy, top-k and the confident-but-wrong calls from a filled sheet")
    met.add_argument("run_dir", type=Path, help="the run the sheet came from (for its candidates file)")
    met.add_argument("--sheet", type=Path, help="the filled sheet, if not the run's own 06_analysis/adjudication.csv")
    met.add_argument("--out", type=Path, help="write the tables here instead of into the run's 06_analysis/")
    args = parser.parse_args(argv)

    run_dir = args.run_dir.resolve()
    if not (run_dir / "05_results" / "species_composition.csv").exists():
        print(f"not a finished run: {run_dir}", file=sys.stderr)
        return 2

    if args.command == "metrics":
        sheet = (args.sheet or layout.analysis_dir(run_dir) / "adjudication.csv").resolve()
        if not sheet.exists():
            print(f"no sheet to count: {sheet}", file=sys.stderr)
            return 2
        result = metrics_module.write_audit(run_dir, sheet, args.out)
        for name, path in result["written"].items():
            print(f"Wrote {path}")
        if result["unjudged"]:
            print(f"  {result['unjudged']} of {result['rows']} row(s) have no outcome yet - counted as unjudged")
        for row in result["table"]:
            if row["Scope"] == metrics_module.SCOPE_ALL:
                print(f"  {row['Rank']:8s} TP {row['TP']:>4} FP {row['FP']:>4} FN {row['FN']:>4}  precision {row['Precision'] or '-':6s} accuracy {row['Accuracy'] or '-'}")
        if "confident_pending" in result:
            print(f"  confident-but-wrong: not counted - {result['confident_pending']}")
        else:
            wrong = result["confident_but_wrong"]
            print(f"  confident calls {result['confident']}: {wrong[metrics_module.CAUSE_MISASSIGNED]} misassigned, "
                  f"{wrong[metrics_module.CAUSE_FOREIGN]} foreign DNA")
        return 0

    library = _library_for(run_dir, args.library)
    if library is None or not library.exists:
        print("no reference library: give --library, or run against a TaxaTag library", file=sys.stderr)
        return 2

    if args.command == "candidates":
        rows = candidates_module.candidate_rows(run_dir, library, args.top)
        path = candidates_module.write_candidates(run_dir, library, args.top)
        summary = candidates_module.summarise(rows)
        print(f"Wrote {path}")
        print(f"  {summary['zotus']} ZOTU(s), {len(rows)} candidate row(s)")
        for name, count in summary.items():
            if name not in ("zotus",) and count:
                print(f"  {count:5d}  {name or 'unflagged'}")
        return 0

    species_list = adjudication_module.SpeciesList.load(args.species_list) if args.species_list else None
    try:
        path = adjudication_module.write_sheet(run_dir, library, species_list)
    except FileExistsError as error:
        print(error, file=sys.stderr)
        return 3
    summary = adjudication_module.summarise(adjudication_module.read_sheet(path))
    print(f"Wrote {path}")
    print(f"  {summary['calls']} call(s), {summary['need attention']} need attention:")
    for name, count in summary.items():
        if name not in ("calls", "need attention") and count:
            print(f"  {count:5d}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

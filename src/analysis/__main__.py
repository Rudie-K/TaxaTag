# src/analysis/__main__.py
"""
The analysis tools from the terminal, so that a run made on a cluster can
be described there too.

    python -m src.analysis candidates <run folder> [--library <folder>] [--top 10]

`<run folder>` is one of the dated folders under `runs/`. The library is
read from the run's own `config_used.yaml` unless given, so the candidates
are named by the library that named the call.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from src.analysis import candidates as candidates_module
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
    args = parser.parse_args(argv)

    run_dir = args.run_dir.resolve()
    if not (run_dir / "05_results" / "species_composition.csv").exists():
        print(f"not a finished run: {run_dir}", file=sys.stderr)
        return 2
    library = _library_for(run_dir, args.library)
    if library is None or not library.exists:
        print("no reference library: give --library, or run against a TaxaTag library", file=sys.stderr)
        return 2

    rows = candidates_module.candidate_rows(run_dir, library, args.top)
    path = candidates_module.write_candidates(run_dir, library, args.top)
    summary = candidates_module.summarise(rows)
    print(f"Wrote {path}")
    print(f"  {summary['zotus']} ZOTU(s), {len(rows)} candidate row(s)")
    for name, count in summary.items():
        if name not in ("zotus",) and count:
            print(f"  {count:5d}  {name or 'unflagged'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

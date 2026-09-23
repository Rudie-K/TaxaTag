# src/analysis/__main__.py
"""
The analysis tools from the terminal, so that a run made on a cluster can
be described there too.

    python -m src.analysis candidates <run folder> [--library <folder>] [--top 10]
    python -m src.analysis adjudication <run folder> [--species-list <csv>] [--library <folder>]
    python -m src.analysis metrics <run folder> [--sheet <filled csv>] [--out <folder>]
    python -m src.analysis coverage --library <folder> --species-list <csv> [--out <csv>] [--markers 12S 16S]
    python -m src.analysis check <run folder> [--sample-sheet <csv> ...] [--rank ...] [--keep-contaminants]
    python -m src.analysis diversity <run folder> [--rank species|genus|family] [--keep-contaminants] [--out <folder>]
                                      [--sample-sheet <csv> [--sample-column Run] [--site-column Site]]

`<run folder>` is one of the dated folders under `runs/`. The library is
read from the run's own `config_used.yaml` unless given, so the candidates
are named by the library that named the call. `metrics` needs no library:
it reads the filled sheet - the run's own, or a copy filled elsewhere -
and the candidates file if it is there. `diversity` needs no library either:
it reads the species table alone. `check` writes nothing: it says what each
analysis would be - available, warned or blocked, and why (decision 0034).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from src.analysis import adjudication as adjudication_module
from src.analysis import candidates as candidates_module
from src.analysis import coverage as coverage_module
from src.analysis import diversity as diversity_module
from src.analysis import metrics as metrics_module
from src.analysis import readiness as readiness_module
from src.analysis import sites as sites_module
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


def _run_options(sub) -> None:
    """The options `diversity` and `check` share, so the check checks what would run."""
    sub.add_argument("run_dir", type=Path, help="a finished run folder (runs/<date>)")
    sub.add_argument("--rank", choices=diversity_module.FIXED_RANKS, help="count every call at this one rank (default: each call at its finest rank, nested calls folded)")
    sub.add_argument("--keep-contaminants", action="store_true", help="count the likely contaminants (human, livestock, pets) instead of setting them aside")
    sub.add_argument("--sample-sheet", type=Path, help="a CSV naming each sample's site; pools replicates into sites")
    sub.add_argument("--sample-column", default="Sample", help="the sheet column holding the run's sample names (default: Sample)")
    sub.add_argument("--site-column", default="Site", help="the sheet column naming each sample's site (default: Site)")


def _print_findings(findings) -> None:
    """Blocked first, then warnings; each kind once, with how many more like it."""
    groups: dict = {}
    for finding in findings:
        groups.setdefault((finding.tier != readiness_module.BLOCKED, finding.code), []).append(finding)
    for (_, _), group in sorted(groups.items()):
        mark = "[blocked]" if group[0].tier == readiness_module.BLOCKED else "[!]"
        more = f" (and {len(group) - 1} more like it)" if len(group) > 1 else ""
        print(f"  {mark} {group[0].text}{more}")


#: What `check` reports on, in the order a user meets them.
_ANALYSES = [("describe the samples", readiness_module.SAMPLE), ("richness", readiness_module.RICHNESS),
             ("Shannon and Simpson", readiness_module.DIVERSITY), ("beta diversity", readiness_module.BETA)]
_SITE_ANALYSES = [("sites", readiness_module.SITES), ("replicate consistency", readiness_module.CONSISTENCY),
                  ("comparing sites", readiness_module.SITE_BETA), ("occurrence", readiness_module.OCCURRENCE)]


def _state_word(where: dict) -> str:
    if where["blocked"]:
        return "blocked"
    bits = ([f"blocked for {len(where['blocked_for'])}"] if where["blocked_for"] else []) + \
           ([f"{len(where['warnings'])} warning(s)"] if where["warnings"] else [])
    return "ok" + (f" ({', '.join(bits)})" if bits else "")


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
    cov = commands.add_parser("coverage", help="which species on a list the library can name at all, per marker (needs no run)")
    cov.add_argument("--library", type=Path, required=True, help="a TaxaTag reference library folder")
    cov.add_argument("--species-list", type=Path, required=True, help="a CSV of species: ScientificName, optional Synonyms")
    cov.add_argument("--out", type=Path, help="write the table here (default: coverage.csv beside the list)")
    cov.add_argument("--markers", nargs="+", help="markers to report (default: every marker the library holds)")
    div = commands.add_parser("diversity", help="richness, Shannon and Simpson diversity, and beta diversity, per sample and marker")
    _run_options(div)
    div.add_argument("--out", type=Path, help="write the tables here instead of into the run's 06_analysis/")
    chk = commands.add_parser("check", help="what each analysis would be on this run - available, warned or blocked - writing nothing")
    _run_options(chk)
    args = parser.parse_args(argv)

    if args.command == "coverage":
        library = ReferenceLibrary(args.library.resolve())
        if not library.exists:
            print(f"no reference library at {args.library}", file=sys.stderr)
            return 2
        species_list = adjudication_module.SpeciesList.load(args.species_list.resolve())
        markers = args.markers or library.available_markers()
        out = (args.out or args.species_list.resolve().with_name("coverage.csv"))
        rows = coverage_module.write_coverage(library, species_list, markers, out)
        print(f"Wrote {out}")
        for marker, counts in coverage_module.summarise(rows).items():
            print(f"  {marker}: {counts['named']} of {counts['total']} species named, {counts['genus']} to the genus only, {counts['absent']} absent")
        return 0

    run_dir = args.run_dir.resolve()
    if not (run_dir / "05_results" / "species_composition.csv").exists():
        print(f"not a finished run: {run_dir}", file=sys.stderr)
        return 2

    if args.command in ("diversity", "check"):
        basis = args.rank or diversity_module.MIXED
        sheet = None
        if args.sample_sheet:
            try:
                sheet = sites_module.SampleSheet.load(args.sample_sheet.resolve(), args.sample_column, args.site_column)
                sites_module.pool(run_dir, sheet, basis, args.keep_contaminants)
            except sites_module.SheetError as error:
                print(error, file=sys.stderr)
                return 2

        if args.command == "check":
            findings = readiness_module.check(run_dir, sheet, basis, args.keep_contaminants)
            loci = sorted(diversity_module.sequenced_samples(run_dir, diversity_module.read_table(run_dir)))
            print(f"Checked {run_dir}")
            for locus in loci:
                shown = _ANALYSES + (_SITE_ANALYSES if sheet is not None else [])
                print(f"  {locus}: " + "; ".join(
                    f"{label} {_state_word(readiness_module.state(findings, affects, locus))}" for label, affects in shown))
            if sheet is None:
                print("  Sites: add --sample-sheet to pool replicates into sites.")
            _print_findings(findings)
            if not findings:
                print("  Nothing is warned or blocked.")
            return 0

        result = diversity_module.write_diversity(run_dir, basis, args.keep_contaminants, args.out)
        for path in result["written"].values():
            print(f"Wrote {path}")
        for row in result["tables"]["summary"]:
            print(f"  {row['Locus']:14s} {row['Sample']:14s} richness {row['Richness']:>3}  "
                  f"Shannon {diversity_module._format(row['Shannon_Diversity']) or '-':>7}  "
                  f"Simpson {diversity_module._format(row['Simpson_Diversity']) or '-':>7}")
        findings = list(result["findings"])
        if sheet is not None:
            site_result = sites_module.write_sites(run_dir, sheet, basis, args.keep_contaminants, args.out)
            for path in site_result["written"].values():
                print(f"Wrote {path}")
            for heading, samples in site_result["tables"]["disagreements"].items():
                if samples:
                    print(f"  samples {heading}: {len(samples)}")
            findings += site_result["findings"]
        _print_findings(findings)
        if findings:
            print(f"  Every caution, with its basis, is in {result['written']['notes'].name}.")
        return 0

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
        wrong, strict = result["confident_but_wrong"], result["corroborated_but_wrong"]
        print(f"  confident calls {result['confident']}: {wrong[metrics_module.CAUSE_MISASSIGNED]} misassigned, "
              f"{wrong[metrics_module.CAUSE_FOREIGN]} foreign DNA, {result['confident_unsupported']} unsupported (unresolved)")
        print(f"  of them corroborated ({metrics_module.CORROBORATED_REFERENCES}+ references) {result['corroborated']}: "
              f"{strict[metrics_module.CAUSE_MISASSIGNED]} misassigned, {strict[metrics_module.CAUSE_FOREIGN]} foreign DNA")
        print(f"  calls the evidence leaves between two or more listed taxa: {result['could_be']} (audit-could-be.csv)")
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

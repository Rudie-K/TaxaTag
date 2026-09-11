# src/validation/run.py
"""
Checking TaxaTag against datasets whose answer is already known.

    python -m src.validation.run list
    python -m src.validation.run fetch --dataset COI-leray --into <folder>
    python -m src.validation.run check --dataset COI-leray --results <run folder>
    python -m src.validation.run all   --library <folder> --work <folder>

Kept apart from the test suite on purpose. The unit tests are offline and
finish in seconds, and should stay that way; this downloads gigabytes from
NCBI and takes as long as a real analysis, so it is run deliberately rather
than on every change.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

from src.gui.primers import PRESETS, complete_locus
from src.pipeline import layout
from src.pipeline.config import PipelineConfig
from src.pipeline.runner import run_pipeline
from src.utils.platform import get_bundled_bin
from src.utils.process import run_tool
from src.utils.reporting import Reporter, console_reporter
from src.validation import datasets as datasets_module

#: How many reads to take from each run. A mock community's answer is decided
#: by its abundant members, which are all present in the first few hundred
#: thousand reads, so there is no need to download tens of millions.
DEFAULT_READ_LIMIT = 200000


def fetch(
    dataset: datasets_module.ValidationDataset,
    into: Path,
    reporter: Reporter,
    read_limit: int = DEFAULT_READ_LIMIT,
) -> List[Path]:
    """Download the runs for a dataset, skipping any already present."""
    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)
    fastq_dump = get_bundled_bin("fastq-dump")

    written: List[Path] = []
    for run in dataset.runs:
        existing = sorted(into.glob(f"{run}_*.fastq*"))
        if existing:
            reporter.info(f"{run}: already downloaded")
            written.extend(existing)
            continue

        reporter.info(f"{run}: downloading up to {read_limit:,} reads")
        command = [fastq_dump, "--split-files", "--outdir", into]
        if read_limit:
            command += ["-X", str(read_limit)]
        command.append(run)

        result = run_tool(
            command, log_path=into / f"{run}_download.log", reporter=reporter,
            heartbeat=f"Downloading {run}",
        )
        if not result.ok:
            reporter.error(f"{run}: download failed - {result.tail(2)}")
            continue
        written.extend(sorted(into.glob(f"{run}_*.fastq*")))

    return written


def _normalise(name: str) -> str:
    return " ".join(str(name).strip().lower().split())


def _genus(name: str) -> str:
    parts = _normalise(name).split()
    return parts[0] if parts else ""


def read_results(species_csv: Path) -> List[Dict[str, str]]:
    if not Path(species_csv).exists():
        return []
    with open(species_csv, "r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def score(
    dataset: datasets_module.ValidationDataset, rows: List[Dict[str, str]]
) -> dict:
    """
    Compare what was found against what should be there.

    Scored at genus as well as species, because a reference library that lacks
    the exact strain still places a sequence in the right genus, and that is a
    materially different outcome from finding nothing or finding the wrong
    thing entirely.
    """
    found_species: Set[str] = set()
    found_genera: Set[str] = set()
    for row in rows:
        name = row.get("Scientific_Name", "")
        if not name or name.lower().startswith("unnamed"):
            continue
        found_species.add(_normalise(name))
        found_genera.add(_genus(name))
        # The lineage columns carry the genus even when the match was only
        # resolved as far as family or class.
        if row.get("Genus"):
            found_genera.add(_normalise(row["Genus"]))

    expected = [_normalise(name) for name in dataset.expected]
    tolerated = {_normalise(name) for name in dataset.tolerated}

    hits, genus_hits, misses = [], [], []
    for original, wanted in zip(dataset.expected, expected):
        # An expected entry may be a genus rather than a binomial, in which
        # case any species in that genus counts.
        as_genus = _genus(wanted)
        if wanted in found_species or any(f.startswith(wanted + " ") for f in found_species):
            hits.append(original)
        elif as_genus in found_genera:
            genus_hits.append(original)
        else:
            misses.append(original)

    expected_genera = {_genus(name) for name in expected}
    unexpected = sorted(
        name for name in found_species
        if _genus(name) not in expected_genera and name not in tolerated
    )

    return {
        "dataset": dataset.key,
        "expected": len(dataset.expected),
        "found_to_species": hits,
        "found_to_genus_only": genus_hits,
        "missed": misses,
        "unexpected": unexpected,
        "total_detected": len(found_species),
    }


def report(result: dict, reporter: Reporter) -> bool:
    """Print a scored result. Returns True when everything expected was found."""
    hits, genus, missed = result["found_to_species"], result["found_to_genus_only"], result["missed"]
    recovered = len(hits) + len(genus)

    reporter.heading(f"{result['dataset']}: {recovered} of {result['expected']} expected taxa recovered")

    for name in hits:
        reporter.success(f"found            {name}")
    for name in genus:
        reporter.info(f"genus only       {name}")
    for name in missed:
        reporter.warning(f"NOT FOUND        {name}")

    if result["unexpected"]:
        reporter.info(
            f"{len(result['unexpected'])} other taxon(s) detected, which for an "
            "environmental sample is normal:"
        )
        for name in result["unexpected"][:10]:
            reporter.debug(f"  also {name}")
        if len(result["unexpected"]) > 10:
            reporter.debug(f"  ...and {len(result['unexpected']) - 10} more")

    return not missed


def analyse(
    dataset: datasets_module.ValidationDataset,
    input_dir: Path,
    output_dir: Path,
    library: Path,
    reporter: Reporter,
) -> Optional[Path]:
    """Run the pipeline over a fetched dataset and return its species table."""
    locus = next(
        (complete_locus(p) for p in PRESETS if p["name"] == dataset.locus), None
    )
    if locus is None:
        reporter.error(f"No primer set called '{dataset.locus}'.")
        return None

    config = PipelineConfig(
        input_dir=Path(input_dir),
        output_base=Path(output_dir),
        convert_sra=False,
        merge_reads=True,
        blast_mode="reference",
        reference_dir=Path(library),
        loci=[locus],
        # A mock community is deliberately uneven, and a member added at one
        # cell in a thousand is still genuinely present, so the proportional
        # filter is relaxed well below the default for a survey.
        min_zotu_size=4,
    )
    config.loci[0]["abundance_filter"] = 0.00001

    result = run_pipeline(config, reporter)
    if result["status"] == "error":
        reporter.error(result["message"])
        return None
    return result.get("final_table")


def command_list(args, reporter) -> int:
    print()
    print(datasets_module.describe_all())
    return 0


def command_fetch(args, reporter) -> int:
    dataset = datasets_module.get(args.dataset)
    if dataset is None:
        print(f"Unknown dataset. Known: {', '.join(datasets_module.DATASETS)}", file=sys.stderr)
        return 2
    files = fetch(dataset, args.into, reporter, args.reads)
    reporter.success(f"{len(files)} file(s) in {args.into}")
    return 0 if files else 1


def command_check(args, reporter) -> int:
    dataset = datasets_module.get(args.dataset)
    if dataset is None:
        print(f"Unknown dataset. Known: {', '.join(datasets_module.DATASETS)}", file=sys.stderr)
        return 2

    results = Path(args.results)
    table = results / layout.IDENTIFICATION / layout.FINAL_TAXONOMY_TABLE
    if not table.exists():
        table = results / layout.IDENTIFICATION / layout.FINAL_SPECIES_TABLE
    if not table.exists():
        table = results
    rows = read_results(table)
    if not rows:
        reporter.error(f"No species table found at {table}")
        return 1

    return 0 if report(score(dataset, rows), reporter) else 1


def command_all(args, reporter) -> int:
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    chosen = args.dataset or list(datasets_module.DATASETS)
    outcomes = []

    for key in chosen:
        dataset = datasets_module.get(key)
        if dataset is None:
            reporter.error(f"Unknown dataset: {key}")
            continue

        reporter.heading(f"=== {dataset.key} ({dataset.marker}) ===")
        reporter.info(dataset.description)

        input_dir = work / key / "reads"
        output_dir = work / key / "results"
        if not fetch(dataset, input_dir, reporter, args.reads):
            outcomes.append((key, False, "no reads downloaded"))
            continue

        table = analyse(dataset, input_dir, output_dir, args.library, reporter)
        if table is None:
            outcomes.append((key, False, "the analysis did not finish"))
            continue

        result = score(dataset, read_results(table))
        ok = report(result, reporter)
        outcomes.append((
            key, ok,
            f"{len(result['found_to_species']) + len(result['found_to_genus_only'])}"
            f"/{result['expected']} recovered",
        ))

    reporter.heading("Summary")
    for key, ok, detail in outcomes:
        (reporter.success if ok else reporter.warning)(f"{key:20s} {detail}")
    return 0 if all(ok for _, ok, _ in outcomes) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taxatag-validate",
        description="Check TaxaTag against datasets whose content is known.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    listing = subparsers.add_parser("list", help="show the available datasets")
    listing.set_defaults(handler=command_list)

    fetching = subparsers.add_parser("fetch", help="download one dataset")
    fetching.add_argument("--dataset", "-d", required=True)
    fetching.add_argument("--into", "-i", required=True, type=Path)
    fetching.add_argument("--reads", type=int, default=DEFAULT_READ_LIMIT)
    fetching.set_defaults(handler=command_fetch)

    checking = subparsers.add_parser("check", help="score results already produced")
    checking.add_argument("--dataset", "-d", required=True)
    checking.add_argument("--results", "-r", required=True, type=Path)
    checking.set_defaults(handler=command_check)

    everything = subparsers.add_parser("all", help="fetch, analyse and score")
    everything.add_argument("--library", "-l", required=True, type=Path)
    everything.add_argument("--work", "-w", required=True, type=Path)
    everything.add_argument("--dataset", "-d", action="append")
    everything.add_argument("--reads", type=int, default=DEFAULT_READ_LIMIT)
    everything.set_defaults(handler=command_all)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, console_reporter())


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    raise SystemExit(main())

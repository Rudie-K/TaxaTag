# src/cli.py
"""
The terminal interface to TaxaTag.

The graphical interface is what most people will use, but a command line is
what makes the tool usable on a computing cluster, inside a script, and in a
method section that someone else has to reproduce.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.pipeline import layout
from src.pipeline.config import PipelineConfig
from src.pipeline.runner import run_pipeline
from src.utils.reporting import console_reporter
from src.utils.validate import validate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taxatag",
        description=(
            "Turn environmental DNA sequencing files into a table of the species "
            "present in each sample."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  taxatag --config config.yaml\n"
            "  taxatag --config config.yaml --input ./reads --output ./results\n"
            "  taxatag --config config.yaml --check-only\n"
            "  taxatag --config config.yaml --stages dereplicate denoise\n"
        ),
    )
    parser.add_argument(
        "--config", "-c", type=Path, default=Path("config.yaml"),
        help="settings file to use (default: config.yaml)",
    )
    parser.add_argument("--input", "-i", type=Path, help="override the input folder")
    parser.add_argument("--output", "-o", type=Path, help="override the results folder")
    parser.add_argument(
        "--blast-mode", choices=["reference", "remote", "local"],
        help=(
            "where to look sequences up: a TaxaTag reference library, NCBI "
            "online, or another BLAST database on this computer"
        ),
    )
    parser.add_argument(
        "--reference", type=Path,
        help="folder holding a TaxaTag reference library (implies --blast-mode reference)",
    )
    parser.add_argument("--blast-db", type=Path, help="path to another BLAST database")
    parser.add_argument("--threads", "-t", type=int, help="CPU threads to use (0 = all)")
    parser.add_argument(
        "--merge-reads", action="store_true",
        help="merge forward and reverse reads before analysis",
    )
    parser.add_argument(
        "--stages", nargs="+", choices=layout.STAGE_ORDER, metavar="STAGE",
        help=f"run only these stages, in order. Choices: {', '.join(layout.STAGE_ORDER)}",
    )
    parser.add_argument(
        "--run-dir", type=Path,
        help="continue inside this existing run folder (default: the most recent)",
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="run the pre-flight checks and stop",
    )
    parser.add_argument(
        "--self-test", action="store_true",
        help=(
            "analyse the bundled samples, whose contents are known, and report "
            "whether the right answer came back. Needs no settings file and no "
            "data of your own"
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="show the tools' own output",
    )
    return parser


def apply_overrides(config: PipelineConfig, args: argparse.Namespace) -> None:
    """Let command-line flags win over the settings file."""
    if args.input:
        config.input_dir = args.input
    if args.output:
        config.output_base = args.output
    if args.blast_mode:
        config.blast_mode = args.blast_mode
    if args.blast_db:
        config.blast_db = args.blast_db
        config.blast_mode = "local"
    if args.reference:
        config.reference_dir = args.reference
        config.blast_mode = "reference"
    if args.threads is not None:
        config.threads = args.threads
    if args.merge_reads:
        config.merge_reads = True


def print_report(report) -> None:
    """Show the pre-flight results as a readable list."""
    symbols = {"ok": "  OK  ", "warning": " NOTE ", "error": "FAILED"}
    print()
    for check in report.checks:
        print(f"[{symbols[check.status]}] {check.name}: {check.message}")
        if check.fix:
            print(f"           -> {check.fix}")
    print(f"\n{report.summary}\n")


def run_self_test(args: argparse.Namespace) -> int:
    """
    Analyse the bundled samples and report whether the answer came back right.

    Deliberately does not require a settings file. Someone checking whether a
    fresh installation works has not written one yet, and being told to create
    a settings file before finding out whether the program runs at all is the
    wrong order.
    """
    from src.validation import selftest

    config = PipelineConfig(output_base=args.output or Path.cwd())
    if args.config.exists():
        try:
            config = PipelineConfig.from_yaml(args.config)
        except Exception:  # noqa: BLE001 - a bad settings file must not block this
            print(f"Ignoring {args.config}: it could not be read.", file=sys.stderr)
    apply_overrides(config, args)

    report = selftest.run(config, console_reporter(verbose=args.verbose))
    print_report(report)
    return 0 if report.can_run else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.self_test:
        return run_self_test(args)

    if not args.config.exists():
        print(f"No settings file at {args.config}", file=sys.stderr)
        print("Create one, or point at an existing one with --config.", file=sys.stderr)
        return 2

    try:
        config = PipelineConfig.from_yaml(args.config)
    except Exception as error:  # noqa: BLE001 - a bad YAML file must read clearly
        print(f"The settings file could not be read: {error}", file=sys.stderr)
        return 2

    apply_overrides(config, args)

    print(f"TaxaTag - {config.project_name}")
    print(f"Settings: {args.config}")

    report = validate(config)
    print_report(report)

    if not report.can_run:
        return 1
    if args.check_only:
        return 0

    reporter = console_reporter(verbose=args.verbose)
    result = run_pipeline(config, reporter, stages=args.stages, run_dir=args.run_dir)

    print()
    if result["status"] == "error":
        print(f"Run failed: {result['message']}", file=sys.stderr)
        return 1
    if result["status"] == "cancelled":
        print("Run stopped before finishing.")
        return 130

    print(f"Results folder: {result['run_dir']}")
    if result.get("final_table"):
        print(f"Species table:  {result['final_table']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

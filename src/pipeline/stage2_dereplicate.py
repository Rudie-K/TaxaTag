# src/pipeline/stage2_dereplicate.py
"""
Stage 2: Dereplication with VSEARCH.

A metabarcoding sample contains the same handful of sequences repeated many
thousands of times over. Collapsing identical reads into one record with a
count attached keeps all of the abundance information while reducing the data
to something the denoising and database-search steps can work through quickly.

Optionally the forward and reverse reads are merged first. That is the better
choice whenever the amplicon is longer than a single read, because the merged
sequence covers the whole marker rather than just its first half.

This stage replaces the USEARCH step in the original pipeline. USEARCH's free
build is 32-bit, memory-limited and not redistributable, which makes it unfit
for a tool that has to install cleanly for someone who does not write code.
VSEARCH is open source, 64-bit, and produces equivalent output.
"""

from __future__ import annotations

import csv
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from src.pipeline import layout
from src.pipeline.config import PipelineConfig
from src.utils.process import remove_quietly, run_tool
from src.utils.sequences import read_lengths
from src.utils.reporting import Reporter, console_reporter

#: Files smaller than this hold no usable reads; trimming emptied them out.
MIN_USABLE_BYTES = 1024


def _parse_derep_stats(text: str) -> Dict[str, int]:
    """Read the sequence counts out of a VSEARCH dereplication report."""
    stats = {"reads": 0, "unique": 0}
    total = re.search(r"(\d+)\s+nt in\s+(\d+)\s+seqs", text)
    if total:
        stats["reads"] = int(total.group(2))
    unique = re.search(r"(\d+)\s+unique sequences", text)
    if unique:
        stats["unique"] = int(unique.group(1))
    return stats


def _parse_merge_stats(text: str) -> Dict[str, int]:
    """Read the pair counts out of a VSEARCH merge report."""
    stats = {"pairs": 0, "merged": 0}
    pairs = re.search(r"(\d+)\s+Pairs", text)
    if pairs:
        stats["pairs"] = int(pairs.group(1))
    merged = re.search(r"(\d+)\s+Merged", text)
    if merged:
        stats["merged"] = int(merged.group(1))
    return stats


#: Below this share of pairs joining, something is wrong with the sample or
#: with the sequencing design, and the user should be told which.
LOW_MERGE_PERCENT = 60.0

#: The overlap assumed when saying how long reads would need to be. The
#: real value is config.min_merge_overlap, which this diagnosis does not
#: have; ten is its default and the difference is a base or two in a figure
#: already stated as a floor.
MIN_OVERLAP_FOR_ESTIMATE = 10


def _why_pairs_may_not_join(r1: Path, locus_config: Optional[Dict]) -> str:
    """
    Say why a sample's read pairs would not join, where the reads can show it.

    Only one cause can be established from the reads alone, and it is
    arithmetic rather than inference: two reads can only overlap if together
    they are longer than the fragment between the primers. A 2x51 run cannot
    span a 163-185 base marker however the software is configured, and no
    setting will change that - the middle of the amplicon was never sequenced.

    Everything else that stops pairs joining - poor quality in the reverse
    read, a mixed library, adapter read-through - looks the same from here, so
    where the arithmetic does not explain it this says so rather than guessing.
    """
    lengths = read_lengths(r1, 500)
    if not lengths:
        return "The reads could not be measured to say why."

    typical = sorted(lengths)[len(lengths) // 2]
    shortest_marker = int((locus_config or {}).get("min_len") or 0)

    if shortest_marker and 2 * typical < shortest_marker:
        # The arithmetic run backwards: how long would the reads have to be?
        # The primers are read as part of each read and trimmed off, so
        # they count against the length; the overlap VSEARCH needs is added
        # on. Any sample tag is not known here, so this is a floor - the
        # message says "at least" and means it.
        primers = (locus_config or {}).get("primers") or {}
        primer_bases = len(primers.get("forward", "")) + len(primers.get("reverse", ""))
        needed_each = -(-(shortest_marker + MIN_OVERLAP_FOR_ESTIMATE + primer_bases) // 2)
        return (
            f"The reads are about {typical} bases each, so a pair covers "
            f"{2 * typical}, and this marker is at least {shortest_marker}. "
            "The middle of the amplicon was never sequenced, so the pairs "
            "cannot overlap - only the forward reads can be used. Reads of at "
            f"least {needed_each} bases each would be needed for the pairs to "
            "meet on this marker; no setting in TaxaTag can make up the "
            "difference."
        )
    return (
        f"The reads are about {typical} bases each, which is long enough to "
        "overlap, so the cause is in the reads themselves - quality, adapter "
        "read-through, or a mixed library. The per-sample logs hold the detail."
    )


def _merge_pairs(
    config: PipelineConfig,
    r1: Path,
    r2: Path,
    merged_path: Path,
    locus_config: Optional[Dict],
    log_path: Path,
    reporter: Reporter,
) -> Optional[Dict[str, int]]:
    """
    Join forward and reverse reads into single full-length sequences.

    No amplicon length window is applied here. The min_len/max_len values in
    the settings describe the amplicon as ordinarily quoted for a primer set,
    which is not the same as the length left after trimming, and enforcing
    them at this point silently discards almost every read for some loci.
    Length is instead measured and reported once ZOTUs exist, where it can be
    checked against the settings rather than assumed to match them.
    """
    command = [
        config.vsearch_path,
        "--fastq_mergepairs", r1,
        "--reverse", r2,
        "--fastqout", merged_path,
        "--fastq_minovlen", str(config.min_merge_overlap),
        "--fastq_allowmergestagger",
        "--threads", str(config.resolve_threads()),
    ]

    result = run_tool(command, log_path=log_path, reporter=reporter)
    if not result.ok:
        reporter.error(f"    merging failed - {result.tail(2)}")
        return None
    return _parse_merge_stats(result.output)


def run_stage2(config: PipelineConfig, reporter: Optional[Reporter] = None) -> dict:
    """Collapse each trimmed sample down to its unique sequences."""
    reporter = reporter or console_reporter()
    reporter.heading(layout.STAGE_TITLES["dereplicate"])

    trimmed_base = layout.trimmed_dir(config.run_dir)
    if not trimmed_base.exists():
        return {
            "status": "error",
            "message": "No trimmed reads found. Run the previous stage first.",
            "summary_csv": None,
            "samples_processed": 0,
        }

    log_dir = layout.logs_dir(config.run_dir, "dereplicate")
    layout.ensure(log_dir, layout.reports_dir(config.run_dir))

    # Work out the full job list up front so progress is meaningful.
    jobs: List[tuple[str, Path]] = []
    for locus_dir in sorted(p for p in trimmed_base.iterdir() if p.is_dir()):
        for r1 in sorted(locus_dir.glob("*_R1.fastq.gz")):
            jobs.append((locus_dir.name, r1))

    if not jobs:
        return {
            "status": "error",
            "message": "No trimmed sequence files were found to dereplicate.",
            "summary_csv": None,
            "samples_processed": 0,
        }

    if config.merge_reads:
        reporter.info("Forward and reverse reads will be merged before dereplication.")
    else:
        reporter.info(
            "Using forward reads only. Switch on read merging if your amplicon "
            "is longer than one read."
        )

    summary_rows = []
    processed_count = 0
    error_count = 0

    scratch_holder = tempfile.TemporaryDirectory(prefix="taxatag_merge_")
    scratch = scratch_holder.name

    for job_index, (locus_name, r1) in enumerate(jobs, start=1):
        reporter.checkpoint()
        sample_name = r1.name[: -len("_R1.fastq.gz")]
        label = f"{sample_name} ({locus_name})"
        reporter.progress(job_index / len(jobs), f"Dereplicating {sample_name}")

        output_dir = layout.dereplicated_dir(config.run_dir, locus_name)
        output_dir.mkdir(parents=True, exist_ok=True)
        unique_fasta = output_dir / f"{sample_name}_unique.fasta"

        if r1.stat().st_size < MIN_USABLE_BYTES:
            reporter.warning(f"{label}: no reads survived trimming - skipped.")
            summary_rows.append([sample_name, locus_name, "Skipped", "Empty after trimming", 0, 0, 0])
            continue

        source = r1
        merged_reads = ""
        if config.merge_reads:
            r2 = r1.parent / f"{sample_name}_R2.fastq.gz"
            if not r2.exists():
                reporter.warning(f"{label}: no reverse file to merge, using forward reads.")
            else:
                # The merged file is written to the system temporary folder
                # rather than into the run folder. It is deleted as soon as it
                # has been dereplicated, so it gains nothing from living with
                # the results - and on Windows a path longer than 260
                # characters cannot be opened by the tools at all, which a
                # deep results folder plus TaxaTag's own nesting reaches
                # easily. The temporary folder is short by construction.
                merged_path = Path(scratch) / f"{sample_name}_merged.fastq"
                locus_config = config.get_locus_by_name(locus_name)
                merge_stats = _merge_pairs(
                    config, r1, r2, merged_path, locus_config,
                    log_dir / f"{sample_name}_{locus_name}_merge.log", reporter,
                )
                if merge_stats is None:
                    summary_rows.append(
                        [sample_name, locus_name, "Failed", "Read merging failed", 0, 0, 0]
                    )
                    error_count += 1
                    continue
                if merge_stats["merged"] == 0:
                    # The explanation belongs here most of all. None merging
                    # is what a genuine gap looks like - a run too short to
                    # reach across the amplicon merges nothing, not a little
                    # - and this branch used to say only that it had
                    # happened. The severest case got the least explanation,
                    # while a half-broken run got the full diagnosis below.
                    reporter.warning(
                        f"{label}: no read pairs could be merged, so only the "
                        "forward reads are used. "
                        + _why_pairs_may_not_join(r1, locus_config)
                    )
                    remove_quietly(merged_path)
                else:
                    pairs = merge_stats["pairs"] or 1
                    percent = 100.0 * merge_stats["merged"] / pairs
                    merged_reads = f"{merge_stats['merged']} merged ({percent:.1f}%)"
                    reporter.debug(f"    {merged_reads}")
                    # Losing most of a sample here is silent otherwise: the
                    # run carries on with whatever merged and reports nothing
                    # unusual, so a library that lost four reads in five looks
                    # the same as one that lost none.
                    if percent < LOW_MERGE_PERCENT:
                        reporter.warning(
                            f"{label}: only {percent:.0f}% of read pairs could be "
                            f"joined, so {100 - percent:.0f}% of this sample was "
                            "not used. " + _why_pairs_may_not_join(r1, locus_config)
                        )
                    source = merged_path

        result = run_tool(
            [
                config.vsearch_path,
                "--fastx_uniques", source,
                "--fastaout", unique_fasta,
                "--sizeout",
                "--relabel", f"{sample_name}.",
                "--strand", "both",
            ],
            log_path=log_dir / f"{sample_name}_{locus_name}_dereplicate.log",
            reporter=reporter,
        )

        # The merged FASTQ is a large intermediate with no downstream use.
        if source != r1:
            remove_quietly(source)

        if not result.ok:
            reporter.error(f"{label}: dereplication failed - {result.tail(2)}")
            summary_rows.append([sample_name, locus_name, "Failed", result.tail(1), 0, 0, 0])
            error_count += 1
            continue

        stats = _parse_derep_stats(result.output)
        reporter.info(
            f"{label}: {stats['reads']:,} reads collapsed to {stats['unique']:,} unique sequences"
        )
        summary_rows.append([
            sample_name, locus_name, "OK", merged_reads or "Forward reads only",
            stats["reads"], stats["unique"],
            round(stats["reads"] / stats["unique"], 1) if stats["unique"] else 0,
        ])
        processed_count += 1

    scratch_holder.cleanup()

    summary_csv = layout.report_csv(config.run_dir, "dereplication")
    with open(summary_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["Sample", "Locus", "Status", "Notes", "Total_Reads", "Unique_Sequences", "Reads_Per_Unique"]
        )
        writer.writerows(summary_rows)

    if processed_count == 0:
        return {
            "status": "error",
            "message": "No sample could be dereplicated.",
            "summary_csv": summary_csv,
            "samples_processed": 0,
        }

    reporter.success(
        f"Dereplicated {processed_count} sample(s)"
        + (f", {error_count} failed" if error_count else "")
    )
    return {
        "status": "ok" if error_count == 0 else "partial",
        "message": f"Dereplicated {processed_count} sample(s).",
        "summary_csv": summary_csv,
        "logs_dir": log_dir,
        "samples_processed": processed_count,
        "errors": error_count,
    }

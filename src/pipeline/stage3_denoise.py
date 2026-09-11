# src/pipeline/stage3_denoise.py
"""
Stage 3: Denoising into ZOTUs.

Sequencing and PCR both introduce errors, so a real biological sequence is
surrounded by a cloud of near-identical variants that differ from it by a base
or two. Denoising folds that cloud back into the true sequence it came from,
leaving zero-radius OTUs (ZOTUs): sequences believed to be genuinely present
in the sample rather than artefacts of the process.

A second step removes chimeras - hybrid sequences formed when a partial PCR
product from one template finishes by copying another. They look like novel
species and are the single most common source of false positives in
metabarcoding, so they are removed before anything reaches the database.

USEARCH's -unoise3 did both jobs in one command. VSEARCH separates them, so
both are run here to reproduce the original pipeline's behaviour.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional

from src.pipeline import layout
from src.pipeline.config import PipelineConfig
from src.utils.process import remove_quietly, run_tool
from src.utils.reporting import Reporter, console_reporter


def _parse_unoise_stats(text: str) -> Dict[str, int]:
    """Read cluster counts out of a VSEARCH cluster_unoise report."""
    stats = {"input": 0, "discarded": 0, "clusters": 0}
    seqs = re.search(r"nt in\s+(\d+)\s+seqs", text)
    if seqs:
        stats["input"] = int(seqs.group(1))
    discarded = re.search(r"minsize \d+:\s+(\d+)\s+sequences discarded", text)
    if discarded:
        stats["discarded"] = int(discarded.group(1))
    clusters = re.search(r"Clusters:\s+(\d+)", text)
    if clusters:
        stats["clusters"] = int(clusters.group(1))
    return stats


def _parse_chimera_stats(text: str) -> Dict[str, int]:
    """Read chimera counts out of a VSEARCH uchime3_denovo report."""
    stats = {"chimeras": 0, "kept": 0}
    found = re.search(r"Found\s+(\d+)\s+\([\d.]+%\)\s+chimeras,\s+(\d+)", text)
    if found:
        stats["chimeras"] = int(found.group(1))
        stats["kept"] = int(found.group(2))
    return stats


def _fasta_lengths(path: Path) -> List[int]:
    """The length of every sequence in a FASTA file."""
    if not path.exists():
        return []
    lengths: List[int] = []
    current = 0
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith(">"):
                if current:
                    lengths.append(current)
                current = 0
            else:
                current += len(line.strip())
    if current:
        lengths.append(current)
    return lengths


def _median(values: List[int]) -> int:
    """The middle value, which unlike the mean is not dragged by outliers."""
    if not values:
        return 0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def run_stage3(config: PipelineConfig, reporter: Optional[Reporter] = None) -> dict:
    """Turn each sample's unique sequences into a set of denoised ZOTUs."""
    reporter = reporter or console_reporter()
    reporter.heading(layout.STAGE_TITLES["denoise"])

    dereplicated_base = layout.dereplicated_dir(config.run_dir)
    if not dereplicated_base.exists():
        return {
            "status": "error",
            "message": "No dereplicated sequences found. Run the previous stage first.",
            "summary_csv": None,
            "samples_processed": 0,
        }

    log_dir = layout.logs_dir(config.run_dir, "denoise")
    layout.ensure(log_dir, layout.reports_dir(config.run_dir))

    jobs: List[tuple[str, Path]] = []
    for locus_dir in sorted(p for p in dereplicated_base.iterdir() if p.is_dir()):
        for fasta in sorted(locus_dir.glob("*_unique.fasta")):
            jobs.append((locus_dir.name, fasta))

    if not jobs:
        return {
            "status": "error",
            "message": "No dereplicated sequence files were found to denoise.",
            "summary_csv": None,
            "samples_processed": 0,
        }

    threads = config.resolve_threads()
    min_size = config.min_zotu_size
    reporter.info(
        f"Discarding sequences seen fewer than {min_size} times before denoising."
    )

    summary_rows = []
    length_warnings: List[str] = []
    processed_count = 0
    error_count = 0

    for job_index, (locus_name, unique_fasta) in enumerate(jobs, start=1):
        reporter.checkpoint()
        sample_name = unique_fasta.name[: -len("_unique.fasta")]
        label = f"{sample_name} ({locus_name})"
        reporter.progress(job_index / len(jobs), f"Denoising {sample_name}")

        output_dir = layout.denoised_dir(config.run_dir, locus_name)
        output_dir.mkdir(parents=True, exist_ok=True)
        with_chimeras = output_dir / f"{sample_name}_denoised_raw.fasta"
        zotus = output_dir / f"{sample_name}_zotus.fasta"

        # --- Denoise -----------------------------------------------------
        denoise_result = run_tool(
            [
                config.vsearch_path,
                "--cluster_unoise", unique_fasta,
                "--centroids", with_chimeras,
                "--minsize", str(min_size),
                "--sizein",
                "--sizeout",
                "--threads", str(threads),
            ],
            log_path=log_dir / f"{sample_name}_{locus_name}_denoise.log",
            reporter=reporter,
        )

        if not denoise_result.ok:
            reporter.error(f"{label}: denoising failed - {denoise_result.tail(2)}")
            summary_rows.append(
                [sample_name, locus_name, "Failed", denoise_result.tail(1), 0, 0, 0, 0, 0, 0]
            )
            error_count += 1
            continue

        denoise_stats = _parse_unoise_stats(denoise_result.output)

        if not _fasta_lengths(with_chimeras):
            # Common and not an error: a sample with few reads can have nothing
            # abundant enough to survive the minimum-size cutoff.
            reporter.warning(
                f"{label}: nothing reached the minimum abundance of {min_size} reads."
            )
            summary_rows.append([
                sample_name, locus_name, "Empty",
                f"No sequence reached {min_size} reads",
                denoise_stats["input"], 0, 0, 0, 0, 0,
            ])
            remove_quietly(with_chimeras)
            continue

        # --- Remove chimeras ---------------------------------------------
        chimera_result = run_tool(
            [
                config.vsearch_path,
                "--uchime3_denovo", with_chimeras,
                "--nonchimeras", zotus,
                "--sizein",
                "--sizeout",
                "--relabel", "Zotu",
            ],
            log_path=log_dir / f"{sample_name}_{locus_name}_chimeras.log",
            reporter=reporter,
        )

        if not chimera_result.ok:
            reporter.error(f"{label}: chimera removal failed - {chimera_result.tail(2)}")
            summary_rows.append([
                sample_name, locus_name, "Failed", chimera_result.tail(1),
                denoise_stats["input"], denoise_stats["clusters"], 0, 0, 0, 0,
            ])
            error_count += 1
            continue

        remove_quietly(with_chimeras)

        chimera_stats = _parse_chimera_stats(chimera_result.output)
        lengths = _fasta_lengths(zotus)
        final_count = len(lengths)
        median_length = _median(lengths)

        reporter.info(
            f"{label}: {denoise_stats['clusters']} denoised, "
            f"{chimera_stats['chimeras']} chimeric, {final_count} ZOTUs kept "
            f"(median length {median_length} bp)"
        )

        # A window that does not contain the observed lengths means the
        # settings describe a different sequence to the one being produced -
        # usually the amplicon with its primers still attached. Worth saying
        # out loud, because it silently ruins any filtering based on it.
        note = ""
        locus_config = config.get_locus_by_name(locus_name)
        if locus_config and final_count:
            low, high = locus_config["min_len"], locus_config["max_len"]
            if median_length and not (low <= median_length <= high):
                note = f"Median length {median_length} bp is outside the configured {low}-{high} bp window"
                length_warnings.append(f"{locus_name}: {note}")

        summary_rows.append([
            sample_name, locus_name, "OK", note,
            denoise_stats["input"], denoise_stats["clusters"], final_count,
            min(lengths) if lengths else 0, median_length, max(lengths) if lengths else 0,
        ])
        processed_count += 1

    summary_csv = layout.report_csv(config.run_dir, "denoising")
    with open(summary_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "Sample", "Locus", "Status", "Notes",
            "Unique_Sequences_In", "Denoised_Clusters", "ZOTUs_After_Chimera_Removal",
            "Min_Length", "Median_Length", "Max_Length",
        ])
        writer.writerows(summary_rows)

    if processed_count == 0:
        return {
            "status": "error",
            "message": (
                "No ZOTUs were produced. The minimum abundance may be too high for "
                "the depth of these samples."
            ),
            "summary_csv": summary_csv,
            "samples_processed": 0,
        }

    for warning in sorted(set(length_warnings)):
        reporter.warning(warning)
    if length_warnings:
        reporter.warning(
            "Length filtering is switched off, so nothing has been discarded because "
            "of this. Adjust the locus length window before switching it on."
        )

    reporter.success(
        f"Produced ZOTUs for {processed_count} sample(s)"
        + (f", {error_count} failed" if error_count else "")
    )
    return {
        "status": "ok" if error_count == 0 else "partial",
        "message": f"Produced ZOTUs for {processed_count} sample(s).",
        "summary_csv": summary_csv,
        "logs_dir": log_dir,
        "samples_processed": processed_count,
        "errors": error_count,
    }

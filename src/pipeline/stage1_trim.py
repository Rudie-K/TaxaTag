# src/pipeline/stage1_trim.py
"""
Stage 1: Primer trimming with Cutadapt.

Metabarcoding reads carry the PCR primers that amplified them. Those primer
bases are not biological signal from the sample, so they have to come off
before sequences can be compared to a reference database.

Which end the primer sits on depends on how long the amplicon is relative to
the read, so this stage samples the start of each file to work out the layout
before choosing how to trim. That check runs once per sample per locus, which
is also how a sample gets assigned to the locus it was actually amplified with.
"""

from __future__ import annotations

import csv
import gzip
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.pipeline import layout
from src.pipeline.config import PipelineConfig
from src.utils.platform import python_module_command
from src.utils.process import run_tool
from src.utils.reporting import Reporter, console_reporter
from src.utils.sequences import read_lengths, primer_pattern

#: How many reads to inspect when working out primer orientation.
SCOUT_READS = 1000

#: How many of those reads must carry the primer before we trust the call.
SCOUT_HIT_THRESHOLD = 20

#: How far into a read to look for a primer signature.
SCOUT_WINDOW = 50

#: Length of the primer fragment used as a search signature. Short enough to
#: survive a sequencing error or two, long enough not to match at random.
SIGNATURE_LENGTH = 12


def cutadapt_workers(threads: int) -> int:
    """
    How many worker processes Cutadapt may use.

    Always one on Windows. Cutadapt's multicore mode is built on Python's
    multiprocessing, and when it is started as a child process whose output is
    a pipe - which is exactly how the pipeline runs it - the workers can finish
    their work, write their output, and then never exit. A run then sits
    forever on a sample that is already done, with no error to explain it.
    This was reproduced on a real dataset: the same sample took 13 seconds
    with one worker and had not finished after three minutes with four.

    Losing the parallelism costs very little. Trimming is not the slow part of
    a run, and samples are processed one after another regardless, so the work
    is spread across cores at that level instead.
    """
    if sys.platform.startswith("win"):
        return 1
    return max(1, threads)


def _swap(primers: Dict) -> Dict:
    """Exchange the forward and reverse primers, and their complements."""
    return {
        "forward": primers["reverse"],
        "reverse": primers["forward"],
        "forward_rc": primers["reverse_rc"],
        "reverse_rc": primers["forward_rc"],
    }


def scout_primers(r1_path: Path, locus_config: Dict) -> Tuple[str, Dict]:
    """
    Work out how this sample's reads are laid out for one locus.

    Returns the trimming mode and the primers to use:

    "Strict-5p"
        The forward primer sits at the start of the read. The amplicon is
        longer than the read, so the primer only appears at the 5' end.
    "3p-ReadThrough"
        The read is longer than the amplicon and has run through into the
        reverse primer, which appears reverse-complemented at the 3' end.
    "No-Primer"
        Neither pattern is present often enough. The sample was almost
        certainly amplified with a different locus's primers.

    Both layouts are also tested the other way round. Which member of a pair
    a sequencing centre calls R1 is a convention, not a fact about the DNA,
    and plenty of public datasets have them the other way up. When that is
    what the reads show, the returned primers are exchanged so the rest of
    the stage can carry on as though they had arrived the usual way.
    """
    primers = locus_config["primers"]

    # Signatures are taken from the primer ends that sit against the read, and
    # matched as patterns rather than as text: a degenerate primer such as
    # mlCOIintF starts "GGWACWGGWTGA", and the letter W never appears in
    # sequencing output, so a plain string comparison finds nothing at all.
    probes = {
        "forward": (primer_pattern(primers["forward"][:SIGNATURE_LENGTH]), "start"),
        "reverse": (primer_pattern(primers["reverse"][:SIGNATURE_LENGTH]), "start"),
        "reverse_rc": (primer_pattern(primers["reverse_rc"][-SIGNATURE_LENGTH:]), "end"),
        "forward_rc": (primer_pattern(primers["forward_rc"][-SIGNATURE_LENGTH:]), "end"),
    }
    hits = dict.fromkeys(probes, 0)

    try:
        opener = gzip.open if r1_path.name.lower().endswith(".gz") else open
        with opener(r1_path, "rt", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle):
                if line_number >= SCOUT_READS * 4:
                    break
                if line_number % 4 != 1:  # only the sequence lines
                    continue
                sequence = line.strip()
                head, tail = sequence[:SCOUT_WINDOW], sequence[-SCOUT_WINDOW:]
                for name, (pattern, where) in probes.items():
                    if pattern.search(head if where == "start" else tail):
                        hits[name] += 1
    except OSError:
        return "No-Primer", {}

    # Whichever layout dominates wins, so a sample showing traces of several
    # is assigned to the one the reads actually support.
    best = max(hits, key=lambda name: hits[name])
    if hits[best] <= SCOUT_HIT_THRESHOLD:
        return "No-Primer", {}

    if best == "forward":
        return "Strict-5p", primers
    if best == "reverse":
        return "Strict-5p", _swap(primers)
    if best == "reverse_rc":
        return "3p-ReadThrough", primers
    return "3p-ReadThrough", _swap(primers)


#: What is left between the primers, below which there is no marker sequence
#: there at all. Every marker TaxaTag handles is at least 160 bases, so this
#: is not a short amplicon - it is the two primers with nothing between them.
DIMER_INSERT_BASES = 20

#: How much of a sample has to be that short before it is worth saying so.
#: Some primer dimer is normal and not worth mentioning; a fifth of the
#: library is a bench problem the user should know about.
SHORT_INSERT_SHARE = 0.20

#: Reads sampled from the trimmed output to measure what survived.
LENGTH_SAMPLE_READS = 2000


def short_insert_note(lengths: List[int], mode: str) -> str:
    """
    Describe a sample whose reads are too short after trimming to be marker.

    Returns an empty string when there is nothing worth saying.

    The claim made here is deliberately narrow. In "3p-ReadThrough" the read
    ran off the end of the amplicon and into the opposite primer, which
    Cutadapt found and removed - so what is left is the amplicon itself,
    measured rather than assumed. Twenty bases of it, for a marker that is at
    least a hundred and sixty, means the two primers were joined to each other
    with no template between them. That is primer dimer, and it is a structural
    fact about the molecule rather than an inference about the sample.

    In "Strict-5p" no such read-through was seen, so the same short reads have
    more than one explanation - a short read length, or quality trimming - and
    the note says only what was measured.
    """
    if not lengths:
        return ""
    short = sum(1 for length in lengths if length < DIMER_INSERT_BASES)
    share = short / len(lengths)
    if share < SHORT_INSERT_SHARE:
        return ""

    percent = f"{100 * share:.0f}%"
    if mode == "3p-ReadThrough":
        return (
            f"{percent} of reads have under {DIMER_INSERT_BASES} bases between "
            "the primers, so the primers were joined to each other rather than "
            "to any marker - primer dimer"
        )
    return (
        f"{percent} of reads are under {DIMER_INSERT_BASES} bases after "
        "trimming, too short to identify"
    )


def build_cutadapt_command(
    r1: Path,
    r2: Path,
    out1: Path,
    out2: Path,
    locus_config: Dict,
    mode: str,
    primers: Dict,
    threads: int,
) -> List[str]:
    """Assemble the Cutadapt call for one sample and locus."""
    # Cutadapt is a Python package rather than a standalone binary, so it is
    # invoked through whichever interpreter is running the pipeline. That
    # resolves to the bundled interpreter in a packaged build.
    command = python_module_command("cutadapt") + ["-j", str(cutadapt_workers(threads))]

    if mode == "Strict-5p":
        command += [
            "-g", primers["forward"],
            "-G", primers["reverse"],
            "--discard-untrimmed",
        ]
    elif mode == "3p-ReadThrough":
        command += [
            "-a", primers["reverse_rc"],
            "-A", primers["forward_rc"],
            "--discard-untrimmed",
        ]

    command += [
        "--revcomp",
        "-e", str(locus_config["error_rate"]),
        "-O", "3",
        "-q", str(locus_config["q_score"]),
        # Only reads trimmed away to nothing are dropped here. The amplicon
        # length window is deliberately NOT applied at this point: trimming
        # acts on each read separately, so a read only covers the full
        # amplicon once the pair has been merged. Filtering on length now
        # would discard every amplicon longer than one read. It is applied
        # during merging, and again to the ZOTUs, where lengths are real.
        "-m", "1",
        "-o", str(out1),
        "-p", str(out2),
        str(r1),
        str(r2),
    ]
    return command


def _parse_cutadapt_log(text: str) -> Tuple[str, str, str]:
    """Pull the read counts out of a Cutadapt report."""
    total = re.search(r"Total read pairs processed:\s+([\d,]+)", text) or re.search(
        r"Total reads processed:\s+([\d,]+)", text
    )
    kept = re.search(
        r"Pairs written \(passing filters\):\s+([\d,]+)\s+\(([\d.]+%)\)", text
    ) or re.search(
        r"Reads written \(passing filters\):\s+([\d,]+)\s+\(([\d.]+%)\)", text
    )

    total_reads = total.group(1).replace(",", "") if total else "0"
    kept_reads = kept.group(1).replace(",", "") if kept else "0"
    rate = kept.group(2) if kept else "0.0%"
    return total_reads, kept_reads, rate


def run_stage1(config: PipelineConfig, reporter: Optional[Reporter] = None) -> dict:
    """Trim primers from every standardised sample, for every configured locus."""
    reporter = reporter or console_reporter()
    reporter.heading(layout.STAGE_TITLES["trim"])

    source_dir = config.standardised_fastq_dir or layout.standardised_dir(config.run_dir)
    if not source_dir or not Path(source_dir).exists():
        return {
            "status": "error",
            "message": "No standardised reads found. Run the previous stage first.",
            "summary_csv": None,
            "samples_processed": 0,
        }

    r1_files = sorted(Path(source_dir).glob("*_1.fastq.gz"))
    if not r1_files:
        return {
            "status": "error",
            "message": f"No standardised sequence files were found in {source_dir}.",
            "summary_csv": None,
            "samples_processed": 0,
        }

    if not config.loci:
        return {
            "status": "error",
            "message": "No loci are configured. Add at least one primer set.",
            "summary_csv": None,
            "samples_processed": 0,
        }

    active_loci = config.active_loci()
    if not active_loci:
        return {
            "status": "error",
            "message": (
                "Every primer set is switched off, so there is nothing to look "
                "for. Tick at least one in the Primer sets tab."
            ),
            "summary_csv": None,
            "samples_processed": 0,
        }
    switched_off = [
        locus.get("name", "unnamed")
        for locus in config.loci
        if locus not in active_loci
    ]
    if switched_off:
        # Said once, at the start. A set that is off produces no lines of its
        # own later, and silence is indistinguishable from a set that matched
        # nothing.
        reporter.info(
            "Not looking for: " + ", ".join(switched_off) + " (switched off)"
        )

    trimmed_base = layout.trimmed_dir(config.run_dir)
    log_dir = layout.logs_dir(config.run_dir, "trim")
    layout.ensure(trimmed_base, log_dir, layout.reports_dir(config.run_dir))

    threads = config.resolve_threads()
    summary_rows = []
    processed_count = 0
    error_count = 0
    total_jobs = len(r1_files)

    for job_index, r1 in enumerate(r1_files, start=1):
        reporter.checkpoint()
        sample_name = re.sub(r"_1\.fastq\.gz$", "", r1.name, flags=re.IGNORECASE)
        r2 = r1.parent / f"{sample_name}_2.fastq.gz"

        reporter.progress(job_index / total_jobs, f"Trimming {sample_name}")

        if not r2.exists():
            reporter.error(f"{sample_name}: reverse read file is missing - skipped.")
            summary_rows.append([sample_name, "-", "Skipped", "ERROR", "0", "0", "0.0%", "reverse read file missing"])
            error_count += 1
            continue

        reporter.info(f"{sample_name}")

        for locus_config in active_loci:
            reporter.checkpoint()
            locus_name = locus_config["name"]
            mode, primers = scout_primers(r1, locus_config)

            if mode == "No-Primer":
                # Expected and harmless: a 12S sample simply has no 16S primers
                # in it. Said out loud rather than logged as debug, because a
                # log that mentions only the marker that matched reads as
                # though every marker matched - and a retention figure of 95%
                # then looks like 95% of the reads matching both, which would
                # mean the primer matching had stopped discriminating.
                reporter.info(
                    f"    {locus_name}: primers not found, so not this marker"
                )
                summary_rows.append(
                    [sample_name, locus_name, "No-Primer", "SKIPPED", "0", "0", "0.0%", ""]
                )
                continue

            safe_mode = re.sub(r"[^A-Za-z0-9._-]", "_", mode)
            out1 = layout.trimmed_dir(config.run_dir, locus_name) / f"{sample_name}_R1.fastq.gz"
            out2 = layout.trimmed_dir(config.run_dir, locus_name) / f"{sample_name}_R2.fastq.gz"
            out1.parent.mkdir(parents=True, exist_ok=True)

            command = build_cutadapt_command(
                r1, r2, out1, out2, locus_config, mode, primers, threads
            )
            result = run_tool(
                command,
                log_path=log_dir / f"{sample_name}_{locus_name}_{safe_mode}.log",
                reporter=reporter,
            )

            total_reads, kept_reads, rate = _parse_cutadapt_log(result.output)

            if result.ok:
                reporter.info(f"    {locus_name}: {mode}, kept {kept_reads} of {total_reads} pairs ({rate})")
                # Kept is not the same as usable. A library can pass trimming
                # with 95% of its pairs and still be mostly primer dimer, and
                # the count alone would never say so.
                note = short_insert_note(
                    read_lengths(out1, LENGTH_SAMPLE_READS), mode
                )
                if note:
                    reporter.warning(f"      {note}")
                summary_rows.append(
                    [sample_name, locus_name, mode, "OK", total_reads, kept_reads, rate, note]
                )
                processed_count += 1
            else:
                reporter.error(f"    {locus_name}: trimming failed - {result.tail(2)}")
                summary_rows.append(
                    [sample_name, locus_name, mode, "ERROR", total_reads, kept_reads, rate,
                     result.tail(1)]
                )
                error_count += 1

    summary_csv = layout.report_csv(config.run_dir, "trimming")
    with open(summary_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["Sample", "Locus", "Mode", "Status", "Total_Read_Pairs", "Kept_Read_Pairs",
             "Kept_Percent", "Note"]
        )
        writer.writerows(summary_rows)

    if processed_count == 0:
        return {
            "status": "error",
            "message": (
                "No sample matched any of the configured primer sets. Check that the "
                "primer sequences in your settings match the loci you sequenced."
            ),
            "summary_csv": summary_csv,
            "samples_processed": 0,
        }

    status = "ok" if error_count == 0 else "partial"
    reporter.success(
        f"Trimmed {processed_count} sample-locus combination(s)"
        + (f", {error_count} failed" if error_count else "")
    )

    return {
        "status": status,
        "message": f"Trimmed {processed_count} sample-locus combination(s).",
        "summary_csv": summary_csv,
        "logs_dir": log_dir,
        "samples_processed": processed_count,
        "errors": error_count,
    }

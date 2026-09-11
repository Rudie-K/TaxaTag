# src/pipeline/stage0_standardise.py
"""
Stage 0: Data standardisation.

Sequencing data arrives in whatever shape the sequencing centre or the public
archive produced it. This stage turns all of it into one predictable layout:

1. Converts raw SRA accessions to FASTQ using fasterq-dump.
2. Sweeps the input folder for FASTQ / FASTQ.GZ files already present.
3. Works out which files are the forward and reverse halves of a pair, and
   joins together lanes that were split across several files.
4. Writes every pair out as <sample>_1.fastq.gz / <sample>_2.fastq.gz.

Everything downstream can then assume that one naming convention.
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from src.pipeline import layout
from src.pipeline.config import PipelineConfig
from src.utils.platform import get_bundled_bin
from src.utils.process import run_tool
from src.utils.reporting import Reporter, console_reporter

#: NCBI run accessions arrive from `prefetch` with no file extension at all.
SRA_ACCESSION = re.compile(r"^(SRR|ERR|DRR)\d+$", re.IGNORECASE)

FASTQ_EXTENSIONS = (".fastq", ".fq", ".fastq.gz", ".fq.gz")

# Which half of a pair a file holds is signalled in many different ways
# depending on who produced it, so we look for the marker both in the middle
# of the name (..._R1_001.fastq) and at the end of it (..._1.fastq).
_R1_MIDDLE = re.compile(r"[-_.](R1|1|F|FWD)[-_.]", re.IGNORECASE)
_R2_MIDDLE = re.compile(r"[-_.](R2|2|R|REV)[-_.]", re.IGNORECASE)
_R1_END = re.compile(r"[-_.](R1|1|F|FWD)$", re.IGNORECASE)
_R2_END = re.compile(r"[-_.](R2|2|R|REV)$", re.IGNORECASE)
_LANE_SUFFIX = re.compile(r"[-_.]L\d+$", re.IGNORECASE)


def find_sra_files(input_dir: Path) -> List[Path]:
    """Every raw SRA archive in a folder, with or without a .sra extension."""
    if not input_dir.exists():
        return []
    return sorted(
        f
        for f in input_dir.iterdir()
        if f.is_file() and (f.suffix.lower() == ".sra" or SRA_ACCESSION.match(f.name))
    )


def _strip_fastq_extension(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".gz"):
        name, lowered = name[:-3], lowered[:-3]
    for ext in (".fastq", ".fq"):
        if lowered.endswith(ext):
            return name[: -len(ext)]
    return name


def _classify_read(filename: str) -> tuple[Optional[str], str]:
    """
    Decide whether a file is the forward or reverse half of a pair.

    Returns (read_direction, sample_id) where read_direction is 'R1', 'R2', or
    None when the file carries no recognisable pairing marker.
    """
    stem = _strip_fastq_extension(filename)

    for pattern_r1, pattern_r2 in ((_R1_MIDDLE, _R2_MIDDLE), (_R1_END, _R2_END)):
        match_r1 = pattern_r1.search(stem)
        match_r2 = pattern_r2.search(stem)
        # An unambiguous match means exactly one of the two patterns hit.
        if match_r1 and not match_r2:
            return "R1", _LANE_SUFFIX.sub("", stem[: match_r1.start()])
        if match_r2 and not match_r1:
            return "R2", _LANE_SUFFIX.sub("", stem[: match_r2.start()])

    return None, stem


def _write_pair(sources: List[Path], destination: Path) -> None:
    """
    Write one or more source FASTQ files out as a single gzip file.

    Several lanes of the same sample are concatenated. Gzip members can be
    joined byte-for-byte, so already-compressed inputs are copied straight
    through without a decompress/recompress round trip.
    """
    already_gzipped = sources[0].name.lower().endswith(".gz")

    if already_gzipped and len(sources) == 1:
        # Nothing to do but put a copy where the pipeline expects it. A hard
        # link avoids duplicating what can be gigabytes of reads; it falls back
        # to a real copy across filesystems or on filesystems without links.
        if destination.exists():
            destination.unlink()
        try:
            os.link(sources[0], destination)
        except OSError:
            shutil.copy2(sources[0], destination)
        return

    # Gzipped sources are concatenated as-is; plain FASTQ is compressed on the
    # way out so that the run folder stays a manageable size.
    open_destination = open if already_gzipped else gzip.open
    with open_destination(destination, "wb") as out:
        for source in sources:
            with open(source, "rb") as handle:
                shutil.copyfileobj(handle, out)


def _standardise_fastq_pairs(
    input_dirs: List[Path], output_dir: Path, reporter: Reporter
) -> dict:
    """Group FASTQ files into pairs and write them out under uniform names."""
    fastq_files: List[Path] = []
    for folder in input_dirs:
        if folder.exists():
            fastq_files.extend(
                f
                for f in folder.iterdir()
                if f.is_file() and f.name.lower().endswith(FASTQ_EXTENSIONS)
            )

    if not fastq_files:
        return {
            "status": "error",
            "message": "No FASTQ files were found to standardise.",
            "samples": [],
            "skipped": [],
        }

    groups: Dict[str, Dict[str, List[Path]]] = {}
    unpaired: List[str] = []

    for path in sorted(fastq_files):
        direction, sample_id = _classify_read(path.name)
        if direction is None:
            unpaired.append(path.name)
            continue
        groups.setdefault(sample_id, {"R1": [], "R2": []})[direction].append(path)

    output_dir.mkdir(parents=True, exist_ok=True)
    processed = []
    skipped = list(unpaired)

    for index, (sample_id, reads) in enumerate(sorted(groups.items()), start=1):
        reporter.checkpoint()
        reporter.progress(index / len(groups), f"Standardising {sample_id}")

        if not reads["R1"] or not reads["R2"] or len(reads["R1"]) != len(reads["R2"]):
            reporter.warning(
                f"{sample_id}: forward and reverse files do not match up "
                f"({len(reads['R1'])} forward, {len(reads['R2'])} reverse) - skipped."
            )
            skipped.extend(p.name for p in reads["R1"] + reads["R2"])
            continue

        reads["R1"].sort()
        reads["R2"].sort()

        destination_1 = output_dir / f"{sample_id}_1.fastq.gz"
        destination_2 = output_dir / f"{sample_id}_2.fastq.gz"

        try:
            if len(reads["R1"]) > 1:
                reporter.info(
                    f"{sample_id}: joining {len(reads['R1'])} lanes per direction"
                )
            _write_pair(reads["R1"], destination_1)
            _write_pair(reads["R2"], destination_2)
            processed.append({"sample": sample_id, "R1": destination_1, "R2": destination_2})
            reporter.debug(f"{sample_id}: written")
        except OSError as error:
            reporter.error(f"{sample_id}: could not be written ({error})")
            skipped.append(sample_id)

    if unpaired:
        reporter.warning(
            f"{len(unpaired)} file(s) had no recognisable forward/reverse marker "
            "and were left out."
        )

    return {
        "status": "ok" if processed else "error",
        "samples": processed,
        "skipped": skipped,
        "message": (
            f"Standardised {len(processed)} sample(s)."
            if processed
            else "No complete forward/reverse pairs could be formed."
        ),
    }


def run_stage0(config: PipelineConfig, reporter: Optional[Reporter] = None) -> dict:
    """Convert and standardise every input file into one uniform FASTQ layout."""
    reporter = reporter or console_reporter()
    reporter.heading(layout.STAGE_TITLES["standardise"])

    config.create_run_directory()
    output_dir = layout.standardised_dir(config.run_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sra_scratch = config.run_dir / ".sra_scratch"

    sra_files = find_sra_files(config.input_dir)
    reporter.info(f"Reading from {config.input_dir}")

    logs = []
    if sra_files and config.convert_sra:
        fasterq_dump = get_bundled_bin("fasterq-dump")
        if not fasterq_dump.exists():
            return {
                "status": "error",
                "message": (
                    "The SRA conversion tool (fasterq-dump) could not be found at "
                    f"{fasterq_dump}. Either install the SRA Toolkit or untick "
                    "'Convert SRA files'."
                ),
                "output_dir": None,
                "logs": [],
                "samples": [],
            }

        sra_scratch.mkdir(parents=True, exist_ok=True)
        log_dir = layout.logs_dir(config.run_dir, "standardise")
        reporter.info(f"Converting {len(sra_files)} SRA file(s) to FASTQ")

        for index, sra in enumerate(sra_files, start=1):
            reporter.checkpoint()
            reporter.progress(index / (len(sra_files) * 2), f"Converting {sra.name}")
            result = run_tool(
                [
                    fasterq_dump,
                    "--split-files",
                    "--threads",
                    str(config.resolve_threads()),
                    "--outdir",
                    sra_scratch,
                    sra,
                ],
                log_path=log_dir / f"{sra.name}_fasterq-dump.log",
                reporter=reporter,
            )
            logs.append(
                {"file": sra.name, "returncode": result.returncode, "output": result.tail()}
            )
            if result.ok:
                reporter.debug(f"{sra.name}: converted")
            else:
                reporter.error(f"{sra.name}: conversion failed - {result.tail(2)}")
    elif sra_files:
        reporter.info(
            f"{len(sra_files)} SRA file(s) present but SRA conversion is switched off."
        )

    result = _standardise_fastq_pairs([config.input_dir, sra_scratch], output_dir, reporter)

    if sra_scratch.exists():
        shutil.rmtree(sra_scratch, ignore_errors=True)

    if result["status"] == "error":
        return {
            "status": "error",
            "message": result["message"],
            "output_dir": output_dir,
            "logs": logs,
            "samples": [],
        }

    config.standardised_fastq_dir = output_dir
    reporter.success(result["message"])

    return {
        "status": result["status"],
        "message": result["message"],
        "output_dir": output_dir,
        "logs": logs,
        "samples": result["samples"],
        "skipped": result.get("skipped", []),
    }

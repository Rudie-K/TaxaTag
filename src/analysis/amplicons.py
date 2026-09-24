# src/analysis/amplicons.py
"""
Every record of a marker volume, cut to the amplicon a primer set reads.

    python -m src.analysis amplicons --library <folder> --locus MiFish_12S --out <folder>

A reference library holds records of many shapes: barcodes deposited
already trimmed, whole mitogenomes, other genes filed under the marker's
volume (planned item 9). A read is none of these; it is the stretch
between the primers. So before a library can be tested with its own
references (planned item 10), each one has to be cut to what a read would
be, and a record that holds no such stretch has to be known.

Gold et al. (2021) built their 12S library with CRUX in two steps, and
this follows them (`planned.md` item 10, *Cutting the queries*):

    primers    a record carrying both primer sites is cut between them, by
               the program's own Cutadapt with the locus's primers and
               error rate - in silico PCR. These cuts are the seeds.
    aligned    every other record is aligned to the seeds with BLAST, and
               the region aligned to a whole seed is its amplicon.

Primers alone would not do: decision 0009 measured that requiring both
primer sites discards 17 of 18 references that work, because deposited
barcodes are usually trimmed of them.

A cut is kept only if it could have been a read: inside the locus's own
length window, aligned over at least the pipeline's own minimum coverage
of a seed. No threshold is invented here. Everything else is `not cut`,
with the reason.

The table is written once per library, locus and setting, and read back
while none of them has changed: a volume is hundreds of thousands of
records.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from src.utils.platform import get_bundled_bin, python_module_command, short_path
from src.utils.process import run_tool
from src.utils.sequences import reverse_complement

PRIMERS, ALIGNED, NOT_CUT = "primers", "aligned", "not cut"

#: Why a record was not cut, in the words the table uses.
NO_SEED = "no seed aligned to it"
PART_OF_A_SEED = "aligned to only part of a seed"
OUTSIDE_WINDOW = "cut outside the primer set's length window"

COLUMNS = ["Record", "Species", "Cut_By", "Start", "End", "Length", "Seed_Coverage", "Seed_Identity",
           "Reason", "Sequence"]

#: The alignment fields read back, in order.
CAPTURE_FIELDS = "qseqid sseqid pident length qstart qend qlen sstart send slen bitscore"

#: The format version of the table and its settings file. A change that
#: alters what a cut is changes this, and every cached table is remade.
FORMAT = 2

#: The seeds are clustered to representatives before the rest are aligned
#: to them. A seed's only job is to show where the amplicon lies in a
#: record, and any homologous seed does that; aligning every record to all
#: of them made the work grow as seeds x similar records. On 16S (60,160
#: distinct seeds, a volume that is all 16S) it would have taken about 20
#: hours. None keeps every seed. The identity was chosen by measurement
#: against the full-seed cut of the 12S volume (decision 0040).
SEED_CLUSTER_IDENTITY = 0.90

#: A BLAST step that writes no new result for this long has stopped, and is
#: ended rather than left to hang (Rudie, 24 September 2026: a long task is
#: fine; one that stops without ending is not). BLAST writes its table a
#: batch of queries at a time, and no batch here takes minutes, let alone
#: half an hour; the whole step may take hours and is never cut short for that.
STALL_SECONDS = 30 * 60
PROGRESS_SECONDS = 5 * 60


def blast_progress(output: Path, position: Dict[str, int], what: str):
    """
    A probe for `run_tool`: how far a BLAST search has got through its
    queries, read from the last line of its table. BLAST takes the queries
    in the order given, so the last query written says how far it is.
    """
    total = len(position)

    def probe():
        # No table yet is no progress yet, not an unreadable probe: a search
        # that hangs before its first result must still count as stalled.
        if not Path(output).exists():
            return 0, f"  {what}: 0 of {total:,}"
        with open(output, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 4096))
            tail = handle.read().decode("utf-8", errors="replace").splitlines()
        lines = [line for line in tail if line.strip()]
        reached = position.get(lines[-1].split("\t", 1)[0], 0) if lines else 0
        return reached, f"  {what}: {reached:,} of {total:,} ({100 * reached / max(1, total):.0f}%)"

    return probe


@dataclass(frozen=True)
class PrimerSet:
    """A locus as the self-check needs it: its primers and the reads it allows."""

    name: str
    marker: str
    forward: str
    reverse: str
    min_len: int
    max_len: int
    error_rate: float

    @classmethod
    def from_locus(cls, locus: Dict) -> "PrimerSet":
        from src.reference.markers import marker_for_locus

        primers = locus.get("primers") or {}
        return cls(
            name=str(locus["name"]),
            marker=marker_for_locus(locus) or "",
            forward=str(primers["forward"]).upper(),
            reverse=str(primers["reverse"]).upper(),
            min_len=int(locus["min_len"]),
            max_len=int(locus["max_len"]),
            error_rate=float(locus.get("error_rate", 0.15)),
        )

    def fits(self, length: int) -> bool:
        return self.min_len <= length <= self.max_len


@dataclass
class Cut:
    record: str
    cut_by: str
    start: int = 0              # 1-based, inclusive, on the record as deposited
    end: int = 0
    sequence: str = ""
    seed_coverage: float = 0.0
    seed_identity: float = 0.0
    reason: str = ""


# ---------------------------------------------------------------- reading the volume


def read_fasta(path: Path) -> Iterator[Tuple[str, str]]:
    """(identifier, sequence) for each record; the identifier is the first word of the header."""
    name, parts = None, []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith(">"):
                if name is not None:
                    yield name, "".join(parts)
                name, parts = line[1:].split()[0] if line[1:].split() else "", []
            elif line:
                parts.append(line)
    if name is not None:
        yield name, "".join(parts)


def dump_volume(volume_dir: Path, volume_name: str, out: Path) -> Path:
    """Write every record of a BLAST volume to a FASTA file, named as BLAST names them."""
    listing = out.with_suffix(".tsv")
    result = run_tool(
        [str(get_bundled_bin("blastdbcmd")), "-db", volume_name, "-entry", "all",
         "-outfmt", "%a\t%s", "-out", str(short_path(listing.parent) / listing.name)],
        cwd=short_path(volume_dir),
    )
    if not result.ok:
        raise RuntimeError(f"could not read the {volume_name} volume: {result.tail(3)}")
    with open(listing, encoding="utf-8") as source, open(out, "w", encoding="utf-8") as target:
        for line in source:
            record, _, sequence = line.rstrip("\n").partition("\t")
            if record and sequence:
                target.write(f">{record}\n{sequence}\n")
    listing.unlink()
    return out


# ---------------------------------------------------------------- the two steps


def cut_by_primers(records: Path, primers: PrimerSet, out: Path, threads: int = 1) -> Dict[str, Cut]:
    """
    In silico PCR: every record carrying both primer sites, cut between them.

    Both primers are required (a linked adapter, each part marked
    `required`), either strand is tried (`--revcomp`), and the cut must fit
    the length window, as a read must.
    """
    linked = f"{primers.forward};required...{reverse_complement(primers.reverse)};required"
    command = python_module_command("cutadapt") + [
        "-j", str(max(1, threads)), "-g", linked, "--revcomp", "--discard-untrimmed",
        "-e", str(primers.error_rate), "-O", "3",
        "-m", str(primers.min_len), "-M", str(primers.max_len),
        "-o", str(out), str(records),
    ]
    result = run_tool(command)
    if not result.ok:
        raise RuntimeError(f"Cutadapt could not cut the seeds: {result.tail(3)}")
    return {
        name: Cut(name, PRIMERS, sequence=sequence.upper(), seed_coverage=100.0, seed_identity=100.0)
        for name, sequence in read_fasta(out)
    }


def unique_seeds(seeds: Dict[str, Cut], out: Path) -> Path:
    """The seeds as a FASTA file, each distinct sequence once."""
    seen: Dict[str, str] = {}
    for cut in seeds.values():
        seen.setdefault(cut.sequence, cut.record)
    with open(out, "w", encoding="utf-8") as handle:
        for index, sequence in enumerate(sorted(seen), 1):
            handle.write(f">seed{index}\n{sequence}\n")
    return out


def representative_seeds(seeds_fasta: Path, identity: float, work: Path, threads: int = 1) -> Path:
    """The seeds clustered by VSEARCH to one centroid per cluster at `identity`."""
    centroids = work / "seed_centroids.fasta"
    result = run_tool([str(get_bundled_bin("vsearch")), "--cluster_fast", str(seeds_fasta), "--id", f"{identity:g}",
                       "--centroids", str(centroids), "--threads", str(max(1, threads)), "--quiet"])
    if not result.ok:
        raise RuntimeError(f"VSEARCH could not cluster the seeds: {result.tail(3)}")
    return centroids


def align_to_seeds(records: Path, seeds_fasta: Path, work: Path, threads: int = 1, reporter=None,
                   order: Optional[Dict[str, int]] = None) -> Path:
    """Align every record to the seeds; the seeds are the database, since they are few."""
    database = work / "seeds"
    made = run_tool(
        [str(get_bundled_bin("makeblastdb")), "-in", seeds_fasta.name, "-dbtype", "nucl", "-out", database.name],
        cwd=short_path(work),
    )
    if not made.ok:
        raise RuntimeError(f"could not index the seeds: {made.tail(3)}")
    table = work / "aligned.tsv"
    searched = run_tool(
        [str(get_bundled_bin("blastn")), "-task", "blastn", "-query", str(short_path(records.parent) / records.name),
         "-db", database.name, "-outfmt", f"6 {CAPTURE_FIELDS}", "-evalue", "1e-10",
         "-max_target_seqs", "5", "-max_hsps", "1", "-num_threads", str(max(1, threads)), "-out", table.name],
        cwd=short_path(work), reporter=reporter,
        progress=blast_progress(table, order, "records aligned") if order else None,
        progress_seconds=PROGRESS_SECONDS, stall_seconds=STALL_SECONDS,
    )
    if not searched.ok:
        raise RuntimeError(f"could not align the records to the seeds: {searched.tail(3)}")
    return table


def cut_by_alignment(alignments: Path, sequences: Dict[str, str], primers: PrimerSet,
                     min_coverage: float) -> Dict[str, Cut]:
    """
    For each record, the stretch homologous to a whole seed.

    The alignment that covers most of its seed wins. Its ends are carried
    out to the seed's ends where the record allows, since an alignment
    often stops a few bases short where primer-adjacent bases differ; that
    is the stretch a read would span. The cut is turned to the seed's
    strand, so every query reads the way the primers do.
    """
    best: Dict[str, Tuple[float, float, List[str]]] = {}
    with open(alignments, encoding="utf-8") as handle:
        for row in csv.reader(handle, delimiter="\t"):
            if len(row) < 11:
                continue
            length, slen = int(row[3]), int(row[9])
            coverage = 100.0 * length / slen if slen else 0.0
            score = (coverage, float(row[10]))
            if row[0] not in best or score > best[row[0]][:2]:
                best[row[0]] = (coverage, float(row[10]), row)
    cuts: Dict[str, Cut] = {}
    for record, (coverage, _, row) in best.items():
        identity = float(row[2])
        qstart, qend, qlen = int(row[4]), int(row[5]), int(row[6])
        sstart, send, slen = int(row[7]), int(row[8]), int(row[9])
        if coverage < min_coverage:
            cuts[record] = Cut(record, NOT_CUT, seed_coverage=coverage, seed_identity=identity,
                               reason=PART_OF_A_SEED)
            continue
        forward = sstart <= send
        seed_low, seed_high = (sstart, send) if forward else (send, sstart)
        before, after = seed_low - 1, slen - seed_high      # seed bases the alignment left out, each end
        if forward:
            start, end = max(1, qstart - before), min(qlen, qend + after)
        else:
            start, end = max(1, qstart - after), min(qlen, qend + before)
        sequence = sequences.get(record, "")[start - 1:end].upper()
        if not forward:
            sequence = reverse_complement(sequence)
        if not primers.fits(len(sequence)):
            cuts[record] = Cut(record, NOT_CUT, start, end, seed_coverage=coverage, seed_identity=identity,
                               reason=OUTSIDE_WINDOW)
            continue
        cuts[record] = Cut(record, ALIGNED, start, end, sequence, coverage, identity)
    return cuts


# ---------------------------------------------------------------- the whole volume


def settings_for(library, primers: PrimerSet, min_coverage: float,
                 seed_identity: Optional[float] = SEED_CLUSTER_IDENTITY) -> Dict[str, object]:
    """What a cached table was made from; any difference means it is remade."""
    volume = library.volume(primers.marker).path
    files = sorted(volume.parent.glob(volume.name + ".*"))
    return {
        "format": FORMAT,
        "library": str(library.root),
        "volume": volume.name,
        "volume_files": {p.name: p.stat().st_size for p in files},
        "locus": primers.name,
        "marker": primers.marker,
        "forward": primers.forward,
        "reverse": primers.reverse,
        "window": [primers.min_len, primers.max_len],
        "error_rate": primers.error_rate,
        "min_coverage": min_coverage,
        "seed_cluster_identity": seed_identity,
    }


def table_path(out_dir: Path, primers: PrimerSet) -> Path:
    return Path(out_dir) / f"amplicons_{primers.name}.tsv"


def cut_volume(library, primers: PrimerSet, out_dir: Path, min_coverage: float, threads: int = 1,
               reporter=None, seed_identity: Optional[float] = SEED_CLUSTER_IDENTITY) -> Path:
    """
    Cut every record of the locus's marker volume, and write the table.

    Read back instead when a table made from the same library, volume and
    settings is already there.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    table = table_path(out_dir, primers)
    stamp = table.with_suffix(".json")
    settings = settings_for(library, primers, min_coverage, seed_identity)
    if table.exists() and stamp.exists():
        try:
            if json.loads(stamp.read_text(encoding="utf-8")) == settings:
                _say(reporter, f"Using the cuts already made: {table.name}")
                return table
        except (OSError, ValueError):
            pass

    species = species_by_record(library, primers.marker)
    work = Path(tempfile.mkdtemp(prefix="taxatag-amplicons-"))
    try:
        volume = library.volume(primers.marker).path
        _say(reporter, f"Reading every record of the {primers.marker} volume...")
        records = dump_volume(volume.parent, volume.name, work / "records.fasta")
        sequences = dict(read_fasta(records))
        _say(reporter, f"  {len(sequences):,} records. Cutting between the primers...")
        seeds = cut_by_primers(records, primers, work / "seeds.fasta", threads)
        if not seeds:
            raise RuntimeError(f"no record carries both {primers.name} primer sites, so there is nothing to "
                               "align the rest to; is this the right locus for this volume?")
        distinct = unique_seeds(seeds, work / "unique_seeds.fasta")
        if seed_identity:
            distinct = representative_seeds(distinct, seed_identity, work, threads)
        count = sum(1 for _ in read_fasta(distinct))
        # A record already cut by its primers needs no alignment: that cut is kept either way.
        rest, order = work / "rest.fasta", {}
        with open(rest, "w", encoding="utf-8") as handle:
            for name, sequence in sequences.items():
                if name not in seeds:
                    handle.write(f">{name}\n{sequence}\n")
                    order[name] = len(order) + 1
        _say(reporter, f"  {len(seeds):,} carry both primer sites ({count:,} representative seeds). "
                       f"Aligning the other {len(sequences) - len(seeds):,} to them...")
        alignments = align_to_seeds(rest, distinct, work, threads, reporter, order)
        aligned = cut_by_alignment(alignments, sequences, primers, min_coverage)
        cuts = {**aligned, **seeds}          # a primer cut is the better evidence, where there is one
        _write_table(table, sequences, cuts, species)
        stamp.write_text(json.dumps(settings, indent=1), encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return table


def species_by_record(library, marker: str) -> Dict[str, str]:
    """Each record's species as the catalogue gives it, with a one-word "species" blanked (0030)."""
    from src.reference.library import binomial_or_blank, clean

    names: Dict[str, str] = {}
    for record, species in library.connect().execute(
            "SELECT accession, species FROM reference_library WHERE marker_gene = ?", (marker,)):
        names[record] = binomial_or_blank({"species": clean(species)})["species"]
    return names


def _write_table(table: Path, records: Iterable[str], cuts: Dict[str, Cut], species: Dict[str, str]) -> None:
    with open(table, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, delimiter="\t")
        writer.writeheader()
        for record in records:
            cut = cuts.get(record) or Cut(record, NOT_CUT, reason=NO_SEED)
            writer.writerow({
                "Record": record, "Species": species.get(record, ""), "Cut_By": cut.cut_by,
                "Start": cut.start or "", "End": cut.end or "",
                "Length": len(cut.sequence) if cut.sequence else "",
                "Seed_Coverage": f"{cut.seed_coverage:.1f}" if cut.cut_by != PRIMERS and cut.seed_coverage else "",
                "Seed_Identity": f"{cut.seed_identity:.2f}" if cut.cut_by != PRIMERS and cut.seed_identity else "",
                "Reason": cut.reason, "Sequence": cut.sequence,
            })


def read_table(table: Path) -> List[Dict[str, str]]:
    with open(table, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def summarise(rows: List[Dict[str, str]]) -> Dict[str, int]:
    counts: Dict[str, int] = {PRIMERS: 0, ALIGNED: 0, NOT_CUT: 0}
    for row in rows:
        counts[row["Cut_By"]] = counts.get(row["Cut_By"], 0) + 1
    return counts


def checksum(rows: List[Dict[str, str]]) -> str:
    """Of the cuts themselves, so a self-check can name the table it used."""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(f"{row['Record']}\t{row['Sequence']}\n".encode("utf-8"))
    return digest.hexdigest()


def _say(reporter, text: str) -> None:
    if reporter is not None:
        reporter.info(text)

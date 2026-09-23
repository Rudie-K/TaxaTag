# src/pipeline/stage4_blast.py
"""
Stage 4: Matching ZOTUs against a reference database.

Every ZOTU is compared against a database of known sequences to find what
organism it came from. Three things make this affordable and trustworthy:

* The same sequence usually turns up in many samples. ZOTUs are pooled and
  de-duplicated before searching, so an identical sequence found in fifty
  samples is looked up once rather than fifty times.
* Sequences are filtered on abundance before searching, not after, so read
  counts too low to be trusted never cost a database query.
* Each marker is searched against reference sequences for that marker only.
  A 12S sequence is never compared against millions of COI records, which is
  both far faster and removes a whole class of spurious match.

A match is judged on two numbers, not one. Percentage identity alone is
misleading: a fragment matching 55 bases of a 256-base sequence perfectly is
reported by BLAST as 100% identity, and would otherwise be recorded as a
confident species identification. Query coverage - how much of the sequence
took part in the alignment - is what separates that from a real match.
"""

from __future__ import annotations

import csv
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.pipeline import layout, ncbi_remote
from src.pipeline.config import PipelineConfig
from src.reference import markers as markers_module
from src.reference.library import LINEAGE_COLUMNS, ReferenceLibrary, ReferenceRecord
from src.utils.accessions import is_placeholder, normalise_accession
from src.utils.process import ToolTimeout, blast_path as _database_argument, run_tool
from src.utils.reporting import Reporter, console_reporter

#: Columns requested from BLAST, in order. qcovhsp is the percentage of the
#: query sequence covered by the alignment.
BLAST_FIELDS = [
    "qseqid", "sseqid", "pident", "length", "qcovhsp", "evalue", "bitscore",
    "sscinames", "staxids",
]

#: A submission can be turned away when NCBI is busy, so it is offered more
#: than once. This is about being *accepted*: once a search has an id, it is
#: waited for rather than sent again.
REMOTE_ATTEMPTS = 3
REMOTE_RETRY_SECONDS = 30

#: The nucleotide collection, which is what a marker sequence is looked up in.
#: NCBI's own default since 2024: everything in `nt` except the genome-assembly
#: chromosomes and scaffolds a barcode never matches. Measured identical to `nt`
#: on 26 hard sequences, 100 hits each (decision 0029); a quarter of the
#: download for anyone fetching it locally.
REMOTE_DATABASE = "core_nt"

#: Sequences seen fewer times than this are never worth a database lookup,
#: regardless of how small the sample was.
ABSOLUTE_MIN_READS = 5

#: What a sequence is called when it matched real references but they carry
#: no name they agree on. It is reported rather than dropped: passing the
#: identity and coverage thresholds means it is biology, and someone checking
#: whether their sequencing worked needs that separated from an artefact that
#: matched nothing at all. Given its own rank so it sorts and filters apart
#: from anything that was actually identified.
UNIDENTIFIED_NAME = "Unidentified"
UNIDENTIFIED_RANK = "Unidentified"

#: Ranks from broadest to finest, used to keep a call no more precise than
#: the reference sequence it was based on.
RANK_ORDER = ["Kingdom", "Phylum", "Class", "Order", "Family", "Genus", "Species"]

#: How much worse than the best hit a reference may score and still be treated
#: as equally good. Expressed as a fraction of the best bitscore.
TIE_MARGIN = 0.01

#: Ranks precise enough to be worth reporting when references had to be
#: reconciled. "Family" is already the coarsest the identity thresholds allow,
#: so a set of references that agree only at phylum or kingdom has not
#: identified anything.
REPORTABLE_RANKS = {"Family", "Genus", "Species"}

#: What can become of a sequence once its hits are read. The last three are
#: discarded, and named like the counts the run reports them under.
REPORTED = "reported"
NO_MATCH = "no_match"
TOO_SHORT = "coverage"
TOO_DISTANT = "identity"


def _read_fasta(path: Path) -> List[Dict]:
    """Read a FASTA file into records, pulling out the ;size= abundance tag."""
    records: List[Dict] = []
    header: Optional[str] = None
    chunks: List[str] = []

    def flush() -> None:
        if header is None:
            return
        sequence = "".join(chunks).upper()
        if not sequence:
            return
        size_match = re.search(r";size=(\d+)", header)
        records.append({
            "id": header.split(";")[0].strip(),
            "size": int(size_match.group(1)) if size_match else 1,
            "sequence": sequence,
        })

    if not path.exists():
        return records

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith(">"):
                flush()
                header = line[1:]
                chunks = []
            elif header is not None:
                chunks.append(line)
    flush()
    return records


def _load_sample_read_totals(run_dir: Path) -> Dict[Tuple[str, str], int]:
    """
    How many reads each sample contributed, per locus.

    The abundance filter is proportional, so it needs the sample's total depth
    rather than just the counts of the ZOTUs that survived denoising.
    """
    totals: Dict[Tuple[str, str], int] = {}
    summary = layout.report_csv(run_dir, "dereplication")
    if not summary.exists():
        return totals
    with open(summary, "r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                totals[(row["Sample"], row["Locus"])] = int(row["Total_Reads"])
            except (KeyError, ValueError):
                continue
    return totals


def _build_blast_command(
    config: PipelineConfig, query: Path, output: Path, database: Optional[Path]
) -> List[str]:
    """
    Assemble the BLAST call for a search against a database on this machine.

    A remote search does not come through here. `blastn -remote` speaks a
    protocol to NCBI that does not reliably complete - see
    `src/pipeline/ncbi_remote.py` - so those go over NCBI's web interface
    instead and come back through `blast_formatter`, which produces these same
    columns.
    """
    return [
        str(config.blastn_path),
        "-query", str(query),
        "-outfmt", "6 " + " ".join(BLAST_FIELDS),
        "-max_target_seqs", str(config.blast_max_target_seqs),
        "-evalue", str(config.blast_evalue),
        "-out", str(output),
        "-db", _database_argument(database),
        "-num_threads", str(config.resolve_threads()),
    ]


def _parse_blast_results(path: Path) -> Dict[str, List[Dict]]:
    """
    Read every hit for each query sequence, best first.

    All of them are kept, not just the strongest. A short marker sequence
    routinely matches many references at exactly the same score, and those
    references do not always agree about what the organism is. Keeping only
    one would mean picking between them at random - in testing, a sequence
    tied against fourteen references was reported as the one snail among ten
    mites, purely because BLAST happened to list it first.
    """
    # Gathered per subject first, so a reference that aligned several times is
    # reduced to its best alignment before anything counts it.
    collected: Dict[str, Dict[str, Dict]] = {}
    if not path.exists():
        return {}

    with open(path, "r", newline="", encoding="utf-8", errors="replace") as handle:
        for row in csv.reader(handle, delimiter="\t"):
            if len(row) < 7:
                continue
            query_id = row[0].replace("lcl|", "").strip()
            try:
                identity = float(row[2])
                coverage = float(row[4])
                bitscore = float(row[6])
            except ValueError:
                continue

            subject = row[1].strip()
            raw_name = row[7].strip() if len(row) > 7 else ""
            taxid = row[8].strip() if len(row) > 8 else ""

            hit = {
                "subject": subject,
                "accession": normalise_accession(subject),
                "identity": identity,
                "coverage": coverage,
                "alignment_length": int(float(row[3])) if row[3] else 0,
                "bitscore": bitscore,
                "scientific_name": "" if is_placeholder(raw_name) else raw_name,
                "taxid": "" if is_placeholder(taxid) else taxid,
            }

            # One row is one aligned region, not one reference, and a reference
            # can align in several places - so the same accession appears more
            # than once. Only its best region is kept, because everything
            # downstream counts these as references: left as they are, a
            # sequence that aligned four times to one accession would look like
            # four references agreeing with each other.
            best = collected.setdefault(query_id, {})
            if subject not in best or bitscore > best[subject]["bitscore"]:
                best[subject] = hit

    return {
        query_id: sorted(hits.values(), key=lambda h: h["bitscore"], reverse=True)
        for query_id, hits in collected.items()
    }


def equally_good(hits: List[Dict], margin: float = TIE_MARGIN) -> List[Dict]:
    """
    The hits that are as good as the best one, within a small margin.

    An exact tie is the obvious case, but a reference scoring a fraction of a
    percent lower is not meaningfully worse either, and treating it as a loser
    would hide a genuine disagreement about what the sequence is.
    """
    if not hits:
        return []
    cutoff = hits[0]["bitscore"] * (1.0 - margin)
    return [hit for hit in hits if hit["bitscore"] >= cutoff]


def evidence_order(hit: Dict) -> Tuple[float, float, float, str]:
    """
    The order a set of hits is read in: score, identity, coverage, then name.

    BLAST lists equal scores in the order the database stores them (Shah et
    al. 2019), which is no order at all, and anything that reads "the first
    one" inherits it. This order is the same on every run, and the name
    decides only between references the evidence cannot tell apart. It is
    also the candidates file's, so the reference the species table reports
    is the first of its tie there that voted for the call.
    """
    return (-hit["bitscore"], -hit["identity"], -hit["coverage"], hit["subject"])


def _supports(hit: Dict, rank: str, thresholds: Optional[Dict[str, float]]) -> bool:
    """
    Whether a reference matched closely enough to name this rank.

    At 98.8% a reference says what genus a sequence is, not what species;
    below the family threshold it says nothing. Ranks above family ask no
    more than family does. Without thresholds, every reference is taken to
    support every rank, which is how references voted before decision 0036.
    """
    if thresholds is None:
        return True
    finest = _assign_rank(hit["identity"], thresholds)
    return finest is not None and RANK_ORDER.index(rank) <= RANK_ORDER.index(finest)


def consensus_assignment(
    tied: List[Dict], lineages: Dict, threshold: float,
    thresholds: Optional[Dict[str, float]] = None,
) -> Tuple[str, str, Dict[str, str], float]:
    """
    Agree on a name across every reference that matched equally well.

    Works from species upwards and stops at the first rank where enough of
    the tied references say the same thing. A sequence matching six records
    that all say the same fish is that fish; one matching mites, snails and
    spiders in equal measure is none of them, and saying so is more useful
    than picking whichever BLAST listed first.

    Given the identity thresholds, each reference votes only at the ranks
    its own identity supports (decision 0036), as the pipelines in the
    literature apply their thresholds hit by hit. Without them every
    reference votes at every rank it is named at.

    Returns the name, the rank it was agreed at, the lineage behind it, and
    how strongly the references agreed.
    """
    voters = [(hit, lineages[hit["subject"]]) for hit in tied if hit["subject"] in lineages]
    if not voters:
        return "", "", {}, 0.0

    # Whether the references actually contradicted each other, as opposed to
    # simply not being named at a rank. The difference decides what a coarse
    # answer means: references that all say "a ray-finned fish" and nothing
    # more are informative, whereas references split between mites, snails and
    # spiders agree only that it is an animal, which is worth nothing.
    contradicted = False

    for column, rank in reversed(LINEAGE_COLUMNS):
        # Only references that are themselves named at this rank get a say.
        # A reference identified no further than its class should not veto a
        # species that every other reference agrees on. Nor should one that
        # matched too distantly to name the rank, whatever it is named.
        named = [
            record.lineage.get(column, "") for hit, record in voters
            if record.lineage.get(column, "") and _supports(hit, rank, thresholds)
        ]
        if not named:
            continue

        counts: Dict[str, int] = {}
        for name in named:
            counts[name] = counts.get(name, 0) + 1
        winner, votes = max(counts.items(), key=lambda item: item[1])
        agreement = votes / len(named)

        if agreement < threshold:
            contradicted = True
            continue

        if contradicted and rank not in REPORTABLE_RANKS:
            # The references disagreed about everything specific enough to be
            # worth reporting, and only converge somewhere as broad as a
            # phylum or kingdom. That is not an identification.
            break

        lineage = next(
            (record.lineage for hit, record in voters
             if record.lineage.get(column) == winner and _supports(hit, rank, thresholds)),
            {},
        )
        # Nothing below the agreed rank can be trusted, so it is dropped
        # rather than carried over from whichever record happened to be picked.
        trimmed, keep = {}, True
        for other_column, _ in LINEAGE_COLUMNS:
            if keep:
                trimmed[other_column] = lineage.get(other_column, "")
            if other_column == column:
                keep = False
        return winner, rank, trimmed, agreement

    return "", "", {}, 0.0


def _database_build(database: Path) -> Optional[Dict[str, str]]:
    """What `tools/fetch_blastdb.py` recorded beside a downloaded NCBI database, if anything."""
    for candidate in (database.parent / "FETCH.json", database.with_name(database.name + "-FETCH.json")):
        if candidate.exists():
            try:
                import json
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
    return None


def _taxonomy_library(config: PipelineConfig):
    """The configured reference library, opened only for its taxonomy, or None."""
    folder = getattr(config, "reference_dir", None)
    if not folder:
        return None
    candidate = ReferenceLibrary(Path(folder))
    return candidate if candidate.exists else None


def lineages_from_taxids(hits: Dict[str, List[Dict]], taxonomy) -> Dict[str, "ReferenceRecord"]:
    """
    A lineage record per hit, keyed like the catalogue's, from the taxid
    BLAST reported for it. A hit that names several taxids ("9606;9598")
    takes the first, as NCBI's own tools do; a hit whose taxid the taxonomy
    does not know is left out and cannot vote.
    """
    taxids = {hit["taxid"].split(";")[0].strip() for group in hits.values() for hit in group if hit.get("taxid")}
    by_taxid = taxonomy.lineages_by_taxid(taxids)
    lineages: Dict[str, ReferenceRecord] = {}
    for group in hits.values():
        for hit in group:
            taxid = (hit.get("taxid") or "").split(";")[0].strip()
            lineage = by_taxid.get(taxid)
            if lineage and any(lineage.values()):
                lineages[hit["subject"]] = ReferenceRecord(
                    accession=hit["subject"], lineage=lineage, source_accession=hit["accession"]
                )
    return lineages


def _assign_rank(identity: float, thresholds: Dict[str, float]) -> Optional[str]:
    """
    Decide how precisely a match can be named.

    A high percentage identity supports naming the species; a weaker one only
    supports the genus or family. Below the family threshold the match is not
    trustworthy enough to report at all.
    """
    for rank in ("species", "genus", "family"):
        if identity >= thresholds.get(rank, 100.0):
            return rank.capitalize()
    return None


def _limit_rank(rank: str, available: str) -> str:
    """
    Never name a match more precisely than the reference sequence itself is.

    A 99.5% match to a reference identified only as far as its genus supports
    a genus-level call, however good the alignment is. A reference carrying no
    usable name supports none, however good the alignment is - so a perfect
    match to something unnamed is reported as unidentified rather than as a
    species.
    """
    if available == UNIDENTIFIED_RANK:
        return UNIDENTIFIED_RANK
    if rank not in RANK_ORDER or available not in RANK_ORDER:
        return rank
    return rank if RANK_ORDER.index(rank) <= RANK_ORDER.index(available) else available


def _trim_to_rank(name: str, rank: str, lineage: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
    """
    A name is never more precise than its rank.

    The identity thresholds can cap the rank below the one the references
    agreed at: a 98.8% match to five references that all say *Trachurus
    trachurus* supports the genus, not the species. The name reported must
    then be the genus, and the lineage below it must go - otherwise one
    column says "Genus" and the next names a species, and a reader trusts
    whichever they read first. Decision 0011's intent; found broken by the
    Sussex Audit's pilot run on 16 September 2026, in four rows of ten
    samples.
    """
    if rank not in RANK_ORDER or not lineage:
        return name, lineage
    column = next((c for c, r in LINEAGE_COLUMNS if r == rank), None)
    if column is None or not lineage.get(column):
        return name, lineage
    trimmed, keep = {}, True
    for other_column, _ in LINEAGE_COLUMNS:
        trimmed[other_column] = lineage.get(other_column, "") if keep else ""
        if other_column == column:
            keep = False
    return lineage[column], trimmed


@dataclass
class Identification:
    """
    What one sequence was called, and which reference the table shows for it.

    `outcome` is REPORTED, or one of the three reasons for a discard, when
    `hit` is the reference that came closest to passing. `tied` is every
    equally good reference, whether or not it could vote; `voters` is how
    many voted at the rank the call was agreed at.
    """
    outcome: str
    hit: Optional[Dict] = None
    tied: List[Dict] = field(default_factory=list)
    name: str = ""
    rank: str = ""
    lineage: Dict[str, str] = field(default_factory=dict)
    agreement: float = 0.0
    voters: int = 0


def identify_sequence(
    group: List[Dict], lineages: Dict, thresholds: Dict[str, float],
    min_coverage: float, agreement_threshold: float,
) -> Identification:
    """
    What one sequence is called, from every hit it had.

    Each equally good reference is judged by its own coverage and its own
    identity, never by whichever one BLAST listed first (decision 0036).
    The Sussex Audit's Zotu83 matched five *Trisopterus luscus* references
    at one score, four at 99.38% and one at 98.78% over a longer alignment.
    The 98.78% one was listed first, and its identity alone capped a
    unanimous species call at genus.

    So a reference covering too little of the sequence, or matching below
    the family threshold, does not vote at all, and one below the species
    threshold still votes for its genus. A call is discarded only when no
    equally good reference passes. The reference reported is the
    best-scoring one that voted for the call, so the identity printed
    always supports the rank beside it.
    """
    if not group:
        return Identification(NO_MATCH)
    tied = equally_good(sorted(group, key=evidence_order))

    # Coverage first: a short alignment can carry a perfect identity, and
    # judging on identity alone is how a fragment becomes a confident false
    # species.
    covering = [hit for hit in tied if hit["coverage"] >= min_coverage]
    if not covering:
        closest = min(tied, key=lambda hit: (-hit["coverage"], evidence_order(hit)))
        return Identification(TOO_SHORT, hit=closest, tied=tied)

    qualified = [hit for hit in covering if _assign_rank(hit["identity"], thresholds)]
    if not qualified:
        closest = min(covering, key=lambda hit: (-hit["identity"], evidence_order(hit)))
        return Identification(TOO_DISTANT, hit=closest, tied=tied)

    if not lineages:
        # Nothing to vote with, so the best-scoring reference that passed
        # names it - chosen the same way on every run.
        hit = qualified[0]
        return Identification(
            REPORTED, hit=hit, tied=tied, name=hit["scientific_name"],
            rank=_assign_rank(hit["identity"], thresholds), agreement=1.0,
        )

    # Every reference that matched equally well gets a say, rather than
    # whichever one BLAST happened to list first.
    name, agreed_rank, lineage, agreement = consensus_assignment(
        qualified, lineages, agreement_threshold, thresholds
    )
    if not name:
        return Identification(
            REPORTED, hit=qualified[0], tied=tied,
            name=UNIDENTIFIED_NAME, rank=UNIDENTIFIED_RANK,
        )

    column = next(c for c, r in LINEAGE_COLUMNS if r == agreed_rank)
    voting = [
        hit for hit in qualified
        if hit["subject"] in lineages and lineages[hit["subject"]].lineage.get(column)
        and _supports(hit, agreed_rank, thresholds)
    ]
    hit = next(h for h in voting if lineages[h["subject"]].lineage[column] == name)
    # The vote already kept each reference to the ranks it supports, so
    # these change nothing today. They are what guarantees the table never
    # prints a rank the identity beside it does not support.
    rank = _assign_rank(hit["identity"], thresholds)
    rank = _limit_rank(rank, agreed_rank)
    name, lineage = _trim_to_rank(name, rank, lineage)
    return Identification(
        REPORTED, hit=hit, tied=tied, name=name, rank=rank,
        lineage=lineage, agreement=agreement, voters=len(voting),
    )


def _write_query(sequences, path: Path) -> Path:
    """Write a batch of sequences out for BLAST to read."""
    with open(path, "w", encoding="utf-8") as handle:
        for sequence, query_id in sequences:
            handle.write(">" + query_id + "\n" + sequence + "\n")
    return path


def _chunks(sequences: List, size: int) -> List[List]:
    """
    Split the sequences into submissions.

    Only remote searches are divided. NCBI's public interface is built for
    interactive-scale queries: a single job of several hundred sequences
    gives no progress while it runs, no partial results if it stalls, and
    nothing for a retry to react to, because a stalled job never returns an
    error. A local database has none of those problems and is fastest asked
    once.
    """
    if size <= 0 or len(sequences) <= size:
        return [sequences]
    return [sequences[i : i + size] for i in range(0, len(sequences), size)]


def _timeout_for(config: PipelineConfig, sequence_count: int) -> Optional[float]:
    """
    How long one submission may take before it is abandoned, in seconds.

    Only remote searches are limited. A local search is bounded by the
    machine it runs on and finishes in seconds; a remote one depends on a
    queue elsewhere and may never come back at all.
    """
    if config.blast_mode != "remote" or config.blast_timeout_minutes <= 0:
        return None
    per_sequence = (config.blast_timeout_minutes * 60.0) / max(1, config.blast_chunk_size)
    return max(120.0, per_sequence * max(1, sequence_count))


def _search_ncbi(
    config: PipelineConfig,
    query_fasta: Path,
    output: Path,
    log_dir: Path,
    log_name: str,
    reporter: Reporter,
    timeout_seconds: Optional[float],
) -> Tuple[bool, str]:
    """
    Search NCBI over their web interface rather than with `blastn -remote`.

    Three steps, and only the middle one takes any time: hand over the
    sequences, wait for NCBI to work through them, then collect the results
    with `blast_formatter`, which writes the same columns a local search does.

    Retrying is deliberately not done here. A search that NCBI has accepted
    has an id and is theirs to finish; submitting it again would put a second
    copy of the same work at the back of the same queue, which is slower than
    waiting and is what made the previous version unable to succeed at all.
    """
    folder = output.parent

    # A search submitted by an earlier attempt at this run is still NCBI's to
    # finish, and they keep a finished one for about a day. Collecting it beats
    # submitting the same work again behind it in the same queue.
    waiting = ncbi_remote.recall(folder, log_name)
    if waiting:
        reporter.info(f"    picking up search {waiting}, submitted earlier")
        try:
            found = ncbi_remote.wait_for(
                ncbi_remote.Submission(rid=waiting, estimated_seconds=0),
                reporter=reporter,
                timeout_seconds=timeout_seconds,
                tool=config.ncbi_tool,
                email=config.ncbi_email,
            )
            return _collect(config, waiting, output, folder, log_dir, log_name,
                            reporter, timeout_seconds, found)
        except ncbi_remote.RemoteSearchError as problem:
            # An expired id is worth replacing; anything else is worth
            # reporting, because the search itself is still pending.
            if "expired" not in str(problem):
                return False, str(problem)
            reporter.info("    that search has expired; submitting it again")
            ncbi_remote.forget(folder, log_name)

    for attempt in range(1, REMOTE_ATTEMPTS + 1):
        reporter.checkpoint()
        try:
            submission = ncbi_remote.submit(
                query_fasta,
                database=REMOTE_DATABASE,
                hitlist_size=config.blast_max_target_seqs,
                expect=config.blast_evalue,
                tool=config.ncbi_tool,
                email=config.ncbi_email,
            )
        except ncbi_remote.RemoteSearchError as refused:
            # Being turned away is worth another try; being told the search
            # failed is not, and that is raised further down.
            if attempt < REMOTE_ATTEMPTS:
                reporter.warning(f"    {refused}; trying again in {REMOTE_RETRY_SECONDS}s")
                _pause(REMOTE_RETRY_SECONDS, reporter)
                continue
            return False, str(refused)

        # Written down before the wait, not after: a search abandoned halfway
        # is exactly the one worth being able to come back to.
        ncbi_remote.remember(folder, log_name, submission.rid, _count_sequences(query_fasta))
        reporter.info(
            f"    NCBI accepted it as search {submission.rid}"
            + (
                f", estimating {submission.estimated_seconds}s"
                if submission.estimated_seconds
                else ""
            )
        )
        try:
            found = ncbi_remote.wait_for(
                submission,
                reporter=reporter,
                timeout_seconds=timeout_seconds,
                tool=config.ncbi_tool,
                email=config.ncbi_email,
            )
        except ncbi_remote.RemoteSearchError as problem:
            return False, f"{problem} Resume will collect it."

        return _collect(config, submission.rid, output, folder, log_dir, log_name,
                        reporter, timeout_seconds, found)

    return False, "the search was never accepted"


def _collect(
    config: PipelineConfig,
    rid: str,
    output: Path,
    folder: Path,
    log_dir: Path,
    log_name: str,
    reporter: Reporter,
    timeout_seconds: Optional[float],
    found: bool,
) -> Tuple[bool, str]:
    """Turn a finished NCBI search into the same table a local search writes."""
    if not found:
        # Finishing with nothing found is a real answer, and an empty file is
        # how the rest of the stage expects to be told so.
        output.write_text("", encoding="utf-8")
        ncbi_remote.forget(folder, log_name)
        return True, ""

    result = run_tool(
        [
            str(ncbi_remote.formatter_path(config.blastn_path)),
            "-rid", rid,
            "-outfmt", "6 " + " ".join(BLAST_FIELDS),
            "-out", str(output),
        ],
        log_path=log_dir / f"{log_name}_collect.log",
        reporter=reporter,
        heartbeat="Collecting the results from NCBI",
        timeout_seconds=timeout_seconds,
    )
    if not result.ok:
        return False, f"search {rid} finished but could not be collected - {result.tail(2)}"
    ncbi_remote.forget(folder, log_name)
    return True, ""


def _count_sequences(fasta: Path) -> int:
    try:
        return sum(1 for line in open(fasta, encoding="utf-8") if line.startswith(">"))
    except OSError:
        return 0


def _pause(seconds: int, reporter: Reporter) -> None:
    """Wait, while still answering the Stop button."""
    for _ in range(seconds):
        reporter.checkpoint()
        time.sleep(1)


def _search_once(
    config: PipelineConfig,
    query_fasta: Path,
    output: Path,
    database: Optional[Path],
    log_dir: Path,
    log_name: str,
    reporter: Reporter,
    timeout_seconds: Optional[float],
) -> Tuple[bool, str]:
    """Run one BLAST submission, retrying it if the search fails or stalls."""
    if config.blast_mode == "remote":
        return _search_ncbi(
            config, query_fasta, output, log_dir, log_name, reporter, timeout_seconds
        )

    command = _build_blast_command(config, query_fasta, output, database)
    attempts = 1
    problem = "no output"

    for attempt in range(1, attempts + 1):
        reporter.checkpoint()
        if attempts > 1:
            reporter.debug(f"    attempt {attempt} of {attempts}")
        reporter.progress(None, "Waiting for the database search")

        try:
            result = run_tool(
                command,
                log_path=log_dir / f"{log_name}_attempt_{attempt}.log",
                reporter=reporter,
                heartbeat=(
                    "Waiting for NCBI"
                    if config.blast_mode == "remote"
                    else "Searching the database"
                ),
                timeout_seconds=timeout_seconds,
            )
            if result.ok:
                return True, ""
            problem = result.tail(2)
        except ToolTimeout as expired:
            # Without a limit this is where a run would sit indefinitely: a
            # search that never returns never fails, so nothing ever retries.
            problem = str(expired)
            reporter.warning(f"    NCBI did not answer - {problem}")

        if attempt < attempts:
            reporter.info(f"    retrying in {REMOTE_RETRY_SECONDS}s")
            for _ in range(REMOTE_RETRY_SECONDS):
                reporter.checkpoint()
                time.sleep(1)

    return False, problem


def _search(
    config: PipelineConfig,
    sequences: List,
    label: str,
    database: Optional[Path],
    output_dir: Path,
    log_dir: Path,
    reporter: Reporter,
) -> Tuple[Dict[str, List[Dict]], List[str], List[Path]]:
    """
    Search one marker's sequences, in as many submissions as it takes.

    Returns the hits found, the sequence ids in any submission that failed,
    and the result files written. Every submission is kept as its own file so
    that a run can be traced, and so that the work already done survives a
    later failure.
    """
    chunk_size = config.blast_chunk_size if config.blast_mode == "remote" else 0
    batches = _chunks(sequences, chunk_size)
    hits: Dict[str, List[Dict]] = {}
    unsearched: List[str] = []
    written: List[Path] = []

    if len(batches) > 1:
        reporter.info(
            f"Searching {len(sequences)} sequence(s) against {label}, "
            f"in {len(batches)} submissions of up to {chunk_size}"
        )
    else:
        reporter.info(f"Searching {len(sequences)} sequence(s) against {label}")

    for index, batch in enumerate(batches, start=1):
        reporter.checkpoint()
        name = f"{label}_{index:03d}" if len(batches) > 1 else label
        query = _write_query(batch, output_dir / f"query_{name}.fasta")
        output = output_dir / f"blast_hits_{name}.tsv"
        written.append(output)

        if len(batches) > 1:
            reporter.info(f"  submission {index} of {len(batches)} ({len(batch)} sequences)")

        ok, problem = _search_once(
            config, query, output, database, log_dir, f"blast_{name}", reporter,
            _timeout_for(config, len(batch)),
        )

        if ok:
            found = _parse_blast_results(output)
            hits.update(found)
            if len(batches) > 1:
                reporter.success(
                    f"  submission {index}: {len(found)} of {len(batch)} matched"
                )
        else:
            reporter.error(f"  submission {index} failed - {problem}")
            unsearched.extend(query_id for _, query_id in batch)

        reporter.progress((index) / len(batches), f"Searched {index} of {len(batches)}")

    return hits, unsearched, written


def _open_library(config: PipelineConfig, reporter: Reporter):
    """Open the configured reference library, or explain why it cannot be."""
    if not config.reference_dir:
        return None, {
            "status": "error",
            "message": (
                "Searching a reference library is selected, but no library folder "
                "has been chosen."
            ),
            "species_csv": None,
        }
    library = ReferenceLibrary(Path(config.reference_dir))
    if not library.exists:
        return None, {
            "status": "error",
            "message": (
                f"No reference library was found at {config.reference_dir}. Choose "
                "the folder that holds taxatag_reference_core.db."
            ),
            "species_csv": None,
        }
    available = library.available_markers()
    reporter.info(f"Using the reference library at {config.reference_dir}")
    reporter.info("Markers available: " + (", ".join(available) or "none"))
    return library, None


def _plan_batches(
    config: PipelineConfig,
    library: Optional[ReferenceLibrary],
    unique_sequences: Dict[str, str],
    query_marker: Dict[str, str],
    reporter: Reporter,
):
    """
    Decide which sequences are searched against which database.

    With a reference library, each marker goes to its own volume so that a 12S
    sequence is never compared against COI records. Otherwise everything goes
    to the single configured database in one batch.
    """
    if library is None:
        database = Path(config.blast_db) if config.blast_mode == "local" else None
        return [("all", database, list(unique_sequences.items()))], None

    by_marker: Dict[str, List] = defaultdict(list)
    without_marker: List = []
    for sequence, query_id in unique_sequences.items():
        marker = query_marker.get(query_id, "")
        (by_marker[marker] if marker else without_marker).append((sequence, query_id))

    if without_marker:
        reporter.warning(
            f"{len(without_marker)} sequence(s) belong to a primer set whose marker "
            "gene could not be worked out from its name, so they cannot be searched. "
            "Set the marker on that primer set."
        )

    scope = (getattr(config, "reference_scope", "") or "").strip()
    batches = []
    missing = []
    unscoped = []
    for marker, sequences in sorted(by_marker.items()):
        volume = library.volume(marker, scope)
        if scope and not volume.exists:
            # Falling back silently would be the worst of the three options:
            # the run would search everything while the settings said it was
            # searching a subset, and the only sign would be a longer species
            # list than expected. Refusing outright would be worse than it
            # sounds too, since a scope is an optimisation rather than a
            # correctness requirement. So it falls back and says so, once.
            unscoped.append(marker)
            volume = library.volume(marker)
        if volume.exists:
            batches.append((marker, volume.path, sequences))
        else:
            missing.append(marker)

    if scope:
        scoped = sorted(set(by_marker) - set(unscoped) - set(missing))
        if scoped:
            reporter.info(f"Searching the '{scope}' subset for: " + ", ".join(scoped))
        if unscoped:
            reporter.warning(
                f"No '{scope}' subset has been built for "
                + ", ".join(sorted(unscoped))
                + ". Those markers were searched against the whole volume."
            )

    if missing:
        reporter.warning(
            "The reference library holds no data for: " + ", ".join(sorted(missing))
            + ". Those sequences cannot be identified."
        )

    if not batches:
        return None, {
            "status": "error",
            "message": (
                "The reference library holds nothing for any marker in this run.\n"
                "Available: " + (", ".join(library.available_markers()) or "none")
                + "\nNeeded: " + (", ".join(sorted(by_marker)) or "none")
            ),
            "species_csv": None,
        }

    return batches, None


def run_stage4(config: PipelineConfig, reporter: Optional[Reporter] = None) -> dict:
    """Search every surviving ZOTU against the reference database."""
    reporter = reporter or console_reporter()
    reporter.heading(layout.STAGE_TITLES["blast"])

    denoised_base = layout.denoised_dir(config.run_dir)
    if not denoised_base.exists():
        return {
            "status": "error",
            "message": "No ZOTUs found. Run the previous stage first.",
            "species_csv": None,
        }

    # ------------------------------------------------------------------
    # Work out what we are searching against
    # ------------------------------------------------------------------
    library: Optional[ReferenceLibrary] = None
    if config.blast_mode == "reference":
        library, failure = _open_library(config, reporter)
        if failure:
            return failure
    elif config.blast_mode == "local":
        if not config.blast_db:
            return {
                "status": "error",
                "message": "Local search is selected but no BLAST database has been chosen.",
                "species_csv": None,
            }
        reporter.info(f"Searching the local database at {config.blast_db}")
        build = _database_build(Path(config.blast_db))
        if build:
            # A snapshot with a date: the write-up has to cite it, and a run that
            # cannot say which build it searched cannot be repeated.
            reporter.info(f"  {build['database']}, NCBI build of {build['ncbi_build']}, "
                          f"{int(build['sequences']):,} sequences (protocols/searching-ncbi.md)")
    else:
        reporter.info(
            "Searching NCBI over the internet. Their servers queue requests, so "
            "this can take anything from a few minutes to several hours. The time "
            "waited is shown as it goes. A local reference library is far faster."
        )

    output_dir = layout.identification_dir(config.run_dir)
    log_dir = layout.logs_dir(config.run_dir, "blast")
    layout.ensure(output_dir, log_dir, layout.reports_dir(config.run_dir))

    read_totals = _load_sample_read_totals(config.run_dir)
    thresholds = {k: float(v) for k, v in config.min_identity.items()}
    min_coverage = float(config.min_query_coverage)

    # ------------------------------------------------------------------
    # Collect ZOTUs and apply the cheap filters before searching anything
    # ------------------------------------------------------------------
    unique_sequences: Dict[str, str] = {}      # sequence -> query id
    query_marker: Dict[str, str] = {}          # query id -> marker
    kept: List[Dict] = []
    audit_rows: List[List] = []
    counts = {"total": 0, "low_abundance": 0, "wrong_length": 0, "kept": 0}

    for locus_dir in sorted(p for p in denoised_base.iterdir() if p.is_dir()):
        locus_name = locus_dir.name
        locus_config = config.get_locus_by_name(locus_name) or {}
        marker = markers_module.marker_for_locus({**locus_config, "name": locus_name}) or ""
        abundance_fraction = config.get_abundance_filter(locus_name)

        for zotu_file in sorted(locus_dir.glob("*_zotus.fasta")):
            reporter.checkpoint()
            sample_name = zotu_file.name[: -len("_zotus.fasta")]
            records = _read_fasta(zotu_file)

            sample_reads = read_totals.get((sample_name, locus_name)) or sum(
                r["size"] for r in records
            )
            min_reads = max(ABSOLUTE_MIN_READS, sample_reads * abundance_fraction)

            for record in records:
                counts["total"] += 1

                if record["size"] < min_reads:
                    counts["low_abundance"] += 1
                    audit_rows.append([
                        sample_name, locus_name, record["id"], "Discarded",
                        f"{record['size']} reads is below the {min_reads:.0f} read minimum "
                        f"({abundance_fraction:.4%} of {sample_reads:,} reads)",
                    ])
                    continue

                if config.apply_length_filter and locus_config:
                    length = len(record["sequence"])
                    if not (locus_config["min_len"] <= length <= locus_config["max_len"]):
                        counts["wrong_length"] += 1
                        audit_rows.append([
                            sample_name, locus_name, record["id"], "Discarded",
                            f"Length {length} bp is outside the "
                            f"{locus_config['min_len']}-{locus_config['max_len']} bp window",
                        ])
                        continue

                sequence = record["sequence"]
                if sequence not in unique_sequences:
                    query_id = f"Seq{len(unique_sequences) + 1:06d}"
                    unique_sequences[sequence] = query_id
                    query_marker[query_id] = marker

                counts["kept"] += 1
                kept.append({
                    "sample": sample_name,
                    "locus": locus_name,
                    "marker": marker,
                    "zotu": record["id"],
                    "size": record["size"],
                    "sequence": sequence,
                    "query_id": unique_sequences[sequence],
                    "sample_reads": sample_reads,
                })

    if not kept:
        return {
            "status": "error",
            "message": (
                "No sequence passed the abundance filter, so there was nothing to "
                "search for. The abundance threshold may be too strict for these samples."
            ),
            "species_csv": None,
        }

    reporter.info(
        f"{counts['total']} ZOTUs: {counts['low_abundance']} too rare"
        + (f", {counts['wrong_length']} wrong length" if counts["wrong_length"] else "")
        + f", {counts['kept']} kept."
    )
    reporter.info(
        f"Those {counts['kept']} come to {len(unique_sequences)} distinct sequences to look up."
    )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    batches, failure = _plan_batches(
        config, library, unique_sequences, query_marker, reporter
    )
    if failure:
        return failure

    hits: Dict[str, List[Dict]] = {}
    blast_outputs: List[str] = []
    unsearched: List[str] = []

    for label, database, sequences in batches:
        reporter.checkpoint()
        found, missed, written = _search(
            config, sequences, label, database, output_dir, log_dir, reporter
        )
        hits.update(found)
        unsearched.extend(missed)
        blast_outputs.extend(str(path) for path in written)

    if unsearched:
        # Some submissions never came back. The work already done is intact,
        # and the run carries on rather than throwing away hours of trimming
        # and denoising over a database that was slow this afternoon.
        #
        # What makes that safe to do is that the gap is recoverable: the run
        # folder keeps everything up to this point, so Resume will search
        # these sequences again without repeating the stages before it. The
        # danger of continuing - a partial table read as a complete one - is
        # answered by saying so here, in the run report, and in the warning
        # the interface shows at the end.
        reporter.warning(
            f"{len(unsearched)} of {len(unique_sequences)} sequence(s) were never "
            "searched, because their submission did not come back. The species "
            "table below is incomplete. Use Resume to search them without "
            "repeating the earlier stages."
        )

    reporter.info(f"{len(hits)} of {len(unique_sequences)} sequences matched something.")

    # ------------------------------------------------------------------
    # Turn identifiers into biology
    # ------------------------------------------------------------------
    lineages = {}
    if library is not None and hits:
        subjects = {hit["subject"] for group in hits.values() for hit in group}
        lineages = library.lookup(subjects)
        reporter.info(
            f"Named {len(lineages)} of {len(subjects)} matched reference sequences "
            "from the local catalogue."
        )
    elif hits:
        # A raw NCBI database, local or remote, has no catalogue of its own -
        # but BLAST reports a taxid for every hit, and a TaxaTag library
        # carries the whole NCBI taxonomy. With that, every equally good hit
        # votes here exactly as it would in a library search, instead of the
        # first-listed hit naming the sequence (decision 0029).
        taxonomy = _taxonomy_library(config)
        if taxonomy is not None:
            lineages = lineages_from_taxids(hits, taxonomy)
            subjects = {hit["subject"] for group in hits.values() for hit in group}
            reporter.info(
                f"Named {len(lineages)} of {len(subjects)} matched sequences by taxid, "
                f"from the taxonomy in the reference library at {taxonomy.root}; "
                "every equally good hit votes."
            )
        else:
            reporter.warning(
                "No reference library is configured, so each sequence takes the name of "
                "its best-scoring hit rather than the agreement of all equally good ones. "
                "Choose a library in the settings to let the hits vote."
            )

    species_rows: List[List] = []
    rejected = {"identity": 0, "coverage": 0, "no_match": 0, "ambiguous": 0}
    threshold = float(config.consensus_threshold)

    for entry in kept:
        call = identify_sequence(
            hits.get(entry["query_id"]) or [], lineages, thresholds, min_coverage, threshold
        )
        hit, tied = call.hit, call.tied
        if call.outcome == NO_MATCH:
            rejected["no_match"] += 1
            audit_rows.append([
                entry["sample"], entry["locus"], entry["zotu"], "No match",
                "Nothing in the database matched this sequence",
            ])
            continue
        if call.outcome == TOO_SHORT:
            rejected["coverage"] += 1
            audit_rows.append([
                entry["sample"], entry["locus"], entry["zotu"], "Discarded",
                f"Only {hit['coverage']:.0f}% of the sequence took part in the match, "
                f"below the {min_coverage:.0f}% minimum (identity was "
                f"{hit['identity']:.2f}% over {hit['alignment_length']} bp"
                + (f"; no other of the {len(tied)} equally good references covered more)"
                   if len(tied) > 1 else ")"),
            ])
            continue
        if call.outcome == TOO_DISTANT:
            rejected["identity"] += 1
            audit_rows.append([
                entry["sample"], entry["locus"], entry["zotu"], "Discarded",
                f"Best match {hit['identity']:.2f}% is below the "
                f"{thresholds.get('family', 95.0):.1f}% minimum",
            ])
            continue

        name, rank, lineage, agreement = call.name, call.rank, call.lineage, call.agreement
        if rank == UNIDENTIFIED_RANK:
            # Reported, not discarded. This sequence matched real references
            # well enough to pass both thresholds - it is biology, not an
            # artefact - and the only thing missing is a name everyone
            # agrees on. Dropping it silently loses the one fact a person
            # validating a dataset most needs: that the sequence is genuine.
            #
            # The same match found through NCBI has always been reported, as
            # "Unnamed sequence ...". Discarding it only when the library is
            # searched made the answer depend on where the search happened
            # rather than on the data.
            rejected["ambiguous"] += 1
            audit_rows.append([
                entry["sample"], entry["locus"], entry["zotu"], "Unidentified",
                f"Matched {len(tied)} reference sequence(s) at "
                f"{hit['identity']:.2f}% over {hit['coverage']:.0f}% of its "
                "length, but they carry no name they agree on. Reported "
                "as a real sequence without an identification.",
            ])

        if lineages:
            record = lineages.get(hit["subject"])
            accession = (record.source_accession if record else "") or hit["accession"]
            taxid = hit.get("taxid", "") if library is None else ""
            support = (
                f"agreed by {agreement:.0%} of the {call.voters} reference(s) able to "
                f"name that rank, of {len(tied)} equally good"
                if call.voters else
                f"agreed by {agreement:.0%} of {len(tied)} equally good reference(s)"
            )
        else:
            accession = hit["accession"]
            taxid = hit["taxid"]
            support = f"the best-scoring of {len(tied)} equally good reference(s)"

        if not name:
            name = f"Unnamed sequence {accession or hit['subject']}"

        species_rows.append([
            entry["sample"], entry["locus"], entry["marker"], entry["zotu"],
            name, accession, taxid, rank,
            round(hit["identity"], 2), round(hit["coverage"], 1),
            len(tied), round(100.0 * agreement, 1),
            entry["size"],
            round(100.0 * entry["size"] / entry["sample_reads"], 4)
            if entry["sample_reads"] else 0.0,
            lineage.get("kingdom", ""), lineage.get("phylum", ""),
            lineage.get("class", ""), lineage.get("order_rank", ""),
            lineage.get("family", ""), lineage.get("genus", ""),
            lineage.get("species", ""),
            entry["sequence"],
        ])
        audit_rows.append([
            entry["sample"], entry["locus"], entry["zotu"], "Kept",
            f"{name} at {hit['identity']:.2f}% identity over {hit['coverage']:.0f}% "
            f"of the sequence ({rank}), {support}, {entry['size']} reads",
        ])
        if len(tied) >= config.blast_max_target_seqs:
            # The tie filled every slot requested, so it is probably larger
            # and the references that voted are whichever BLAST listed first.
            # Said here rather than silently, because a consensus over an
            # arbitrary sample of a tie once named the most abundant sequence
            # in a dataset from ten mislabelled records out of three thousand.
            rejected["tie at cap"] = rejected.get("tie at cap", 0) + 1
            audit_rows.append([
                entry["sample"], entry["locus"], entry["zotu"], "Tie at cap",
                f"{len(tied)} equally good references is the maximum requested "
                f"(blast_max_target_seqs); the tie may be larger, and which "
                "references voted was BLAST's choice, not the best available.",
            ])

    species_csv = output_dir / layout.FINAL_SPECIES_TABLE
    with open(species_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "Sample", "Locus", "Marker", "ZOTU", "Scientific_Name", "Accession",
            "TaxID", "Rank", "Identity_Percent", "Query_Coverage_Percent",
            "References_Matched", "Agreement_Percent",
            "Reads", "Percent_Of_Sample",
            "Kingdom", "Phylum", "Class", "Order", "Family", "Genus", "Species",
            "Sequence",
        ])
        writer.writerows(species_rows)

    audit_csv = layout.report_csv(config.run_dir, "identification_audit")
    with open(audit_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Sample", "Locus", "ZOTU", "Decision", "Reason"])
        writer.writerows(audit_rows)

    if library is not None:
        library.close()

    if not species_rows:
        return {
            "status": "error",
            "message": (
                "Nothing matched the database well enough to be identified. "
                f"{rejected['no_match']} had no match, {rejected['coverage']} matched "
                f"over too little of their length, {rejected['identity']} were below "
                f"the identity threshold, and {rejected['ambiguous']} matched "
                "references that disagree."
            ),
            "species_csv": species_csv,
            "audit_csv": audit_csv,
        }

    distinct = len({row[4] for row in species_rows})
    reporter.success(
        f"Identified {distinct} distinct taxa across {len(species_rows)} sample records."
    )
    if any(rejected.values()):
        reporter.info(
            f"{rejected['no_match']} with no match, {rejected['coverage']} matching too "
            f"little of their length, {rejected['identity']} below the identity "
            f"threshold, {rejected['ambiguous']} too ambiguous to name. "
            f"See {audit_csv.name} for the reason behind every decision."
        )
    if rejected.get("tie at cap"):
        reporter.warning(
            f"{rejected['tie at cap']} call(s) matched as many references as were "
            f"requested ({config.blast_max_target_seqs}); their ties may be larger. "
            "A higher blast_max_target_seqs in the settings lets every equally good "
            "reference vote."
        )

    # Whether lineages came from the local catalogue decides whether the next
    # stage needs the internet at all.
    lineage_rows = sum(1 for row in species_rows if row[14] or row[15])

    # A table built from only some of the sequences must not report itself as
    # a finished one. "partial" is what makes the run end with a warning
    # rather than a tick, and it is the difference between a user resuming and
    # a user publishing.
    return {
        "status": "partial" if unsearched else "ok",
        "message": (
            f"Identified {distinct} distinct taxa, but {len(unsearched)} "
            f"sequence(s) were never searched - use Resume to finish them."
            if unsearched
            else f"Identified {distinct} distinct taxa."
        ),
        "unsearched": len(unsearched),
        "species_csv": species_csv,
        "audit_csv": audit_csv,
        "blast_hits": blast_outputs,
        "taxa_found": distinct,
        "records": len(species_rows),
        "lineage_from_library": lineage_rows == len(species_rows) and lineage_rows > 0,
    }

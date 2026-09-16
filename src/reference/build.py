# src/reference/build.py
"""
Building and extending a reference library.

Adding a marker means three things happening together:

1. Reference sequences are read from a source database.
2. Each is given a lineage, resolved through NCBI taxonomy so that sources
   using different naming conventions end up comparable.
3. The sequences go into a BLAST volume for that marker, and their lineages
   into the SQLite catalogue, keyed by the same identifier so a search result
   can be turned back into a species.

Markers are added one at a time and never disturb each other. Rebuilding a
whole library from scratch takes hours, and needing to do that just to add
one marker would make the library impractical to maintain.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from src.reference import sources as sources_module
from src.reference.library import (
    CATALOGUE_NAME,
    LINEAGE_COLUMNS,
    VOLUMES_DIR,
    is_unknown,
)
from src.utils.process import run_tool
from src.utils.reporting import Reporter, console_reporter

#: Phyla that can plausibly turn up in a marine or coastal water sample.
#: Used to keep a COI volume to a workable size: the global barcode library
#: runs to over twenty million records, most of them land animals that cannot
#: be in the water, and every one of them costs disk space and search time.
MARINE_PHYLA: Set[str] = {
    "annelida", "arthropoda", "brachiopoda", "bryozoa", "chaetognatha",
    "chordata", "cnidaria", "ctenophora", "echinodermata", "entoprocta",
    "gastrotricha", "gnathostomulida", "hemichordata", "kinorhyncha",
    "loricifera", "mollusca", "nemertea", "nematoda", "phoronida",
    "placozoa", "platyhelminthes", "porifera", "priapulida", "rotifera",
    "sipuncula", "tardigrada", "xenacoelomorpha",
    # Algae, seaweeds and protists, which the 18S and COI markers both reach.
    "rhodophyta", "chlorophyta", "ochrophyta", "haptophyta", "bacillariophyta",
    "dinoflagellata", "ciliophora", "cercozoa", "foraminifera", "radiozoa",
}

#: Classes to leave out even though their phylum is kept.
#:
#: Arthropoda and Chordata both have to be kept at phylum level, because each
#: holds the marine groups that matter most - crustaceans, fish, marine
#: mammals - alongside entirely terrestrial ones. Filtering them by class is
#: what stops "keep Arthropoda" from meaning "keep every insect in the world",
#: which is the bulk of the barcode library.
#:
#: Insects do wash into coastal water and can appear in a real sample. They
#: are excluded anyway because they are never the target of a marine survey,
#: and carrying millions of them makes every COI search slower for everyone.
#: Remove a name from this set and rebuild to keep that group.
#: Names have to be listed as the source files actually write them, not as a
#: textbook arranges them. "entognatha" alone was not enough: BOLD files
#: springtails under the class Collembola, so 104,606 of them passed straight
#: through a filter that believed it had excluded them - 8% of the COI library,
#: and the second most abundant genus in it after Homo sapiens. Both spellings
#: are listed rather than one being chosen, because either may appear.
EXCLUDED_CLASSES: Set[str] = {
    "insecta",          # by far the largest group in the archive
    "arachnida",        # spiders, mites and ticks
    "diplopoda",        # millipedes
    "chilopoda",        # centipedes
    "entognatha",       # springtails and their relatives, as a superclass
    "collembola",       # springtails, as BOLD and NCBI actually file them
    "protura",          # coneheads, the other entognath classes
    "diplura",          # two-pronged bristletails
}

#: How many records to insert per transaction. Large enough that the overhead
#: of committing disappears, small enough that a failure does not lose much.
BATCH_SIZE = 20000


@dataclass
class SourceSpec:
    """One source file feeding a marker build, with its own selection rules."""

    path: Path
    source: str                  # a key in sources.PARSERS
    min_length: int = 0          # shortest sequence worth keeping
    max_length: int = 0          # 0 means no upper limit
    note: str = ""               # why this source is here, for the log
    #: accession -> NCBI taxid, for a source whose headers carry no taxonomy
    #: of their own. MitoFish is the case in point: its headers are a bare
    #: accession, and the mapping lives in a separate file.
    taxid_map: Optional[Dict[str, str]] = None

    def __post_init__(self) -> None:
        self.path = Path(self.path)


@dataclass
class BuildResult:
    """What a marker build produced."""

    marker: str
    written: int = 0
    skipped_no_lineage: int = 0
    skipped_not_marine: int = 0
    skipped_incomplete: int = 0
    skipped_duplicate: int = 0
    skipped_same_sequence: int = 0
    skipped_land_surplus: int = 0
    volume: Optional[Path] = None

    @property
    def considered(self) -> int:
        return (
            self.written + self.skipped_no_lineage + self.skipped_not_marine
            + self.skipped_incomplete + self.skipped_duplicate
            + self.skipped_same_sequence + self.skipped_land_surplus
        )

    def describe(self) -> str:
        parts = [f"{self.written:,} written"]
        if self.skipped_no_lineage:
            parts.append(f"{self.skipped_no_lineage:,} with no usable lineage")
        if self.skipped_not_marine:
            parts.append(f"{self.skipped_not_marine:,} not marine")
        if self.skipped_incomplete:
            parts.append(f"{self.skipped_incomplete:,} not identified to family")
        if self.skipped_duplicate:
            parts.append(f"{self.skipped_duplicate:,} repeated identifiers")
        if self.skipped_same_sequence:
            parts.append(f"{self.skipped_same_sequence:,} identical sequences")
        if self.skipped_land_surplus:
            parts.append(
                f"{self.skipped_land_surplus:,} surplus land-class references "
                f"(kept {LAND_REFERENCES_PER_SPECIES} per species)"
            )
        return ", ".join(parts)


class TaxonomyResolver:
    """
    Turns whatever a source knows about an organism into a 7-rank lineage.

    Prefers the NCBI taxid when the source supplies one, because a taxid is
    unambiguous where a name is not: several unrelated organisms share a name,
    and one organism has many synonyms. Falls back to matching by name, which
    is what sources without taxids leave available.
    """

    def __init__(self, connection: sqlite3.Connection, reporter: Reporter):
        self.connection = connection
        self.reporter = reporter
        self._by_taxid: Dict[str, Tuple] = {}
        self._by_name: Dict[str, Tuple] = {}
        self._loaded = False

    def load(self) -> None:
        """
        Read NCBI taxonomy into memory.

        Around 2.9 million rows, which is a few hundred megabytes held for the
        duration of a build. That is worth it: resolving tens of millions of
        records one query at a time would take many hours.
        """
        if self._loaded:
            return
        self.reporter.info("Loading NCBI taxonomy into memory...")
        columns = ", ".join(column for column, _ in LINEAGE_COLUMNS)
        cursor = self.connection.execute(
            f"SELECT tax_id, {columns} FROM ncbi_taxonomy_matrix"
        )
        for row in cursor:
            lineage = tuple(row[1:])
            self._by_taxid[str(row[0])] = lineage
            species = row[7]
            if species and not is_unknown(species):
                self._by_name.setdefault(species.strip().lower(), lineage)
        self._loaded = True
        self.reporter.info(
            f"Loaded {len(self._by_taxid):,} taxa ({len(self._by_name):,} names)."
        )

    def lineage_of(self, taxid: str) -> Optional[Dict[str, str]]:
        """The 7-rank lineage of one NCBI taxid, placeholders blanked, or None."""
        self.load()
        lineage = self._by_taxid.get(str(taxid))
        if lineage is None:
            return None
        return {column: ("" if is_unknown(value) else str(value).strip())
                for (column, _), value in zip(LINEAGE_COLUMNS, lineage)}

    def resolve(self, record) -> Optional[Dict[str, str]]:
        """The lineage for one source record, or None when it cannot be placed."""
        self.load()
        lineage = None
        if record.taxid:
            lineage = self._by_taxid.get(record.taxid)
        if lineage is None and record.name:
            lineage = self._by_name.get(record.name.strip().lower())
        if lineage is None:
            # A source that carried its own lineage can still be used.
            if record.lineage:
                return {column: record.lineage.get(column, "") for column, _ in LINEAGE_COLUMNS}
            # Failing everything else, the name alone is better than nothing.
            if record.name:
                return {column: "" for column, _ in LINEAGE_COLUMNS} | {
                    "species": record.name
                }
            return None
        return {
            column: ("" if is_unknown(value) else str(value).strip())
            for (column, _), value in zip(LINEAGE_COLUMNS, lineage)
        }


#: Some sources bundle identical sequences from several accessions under one
#: header, joined with semicolons - MitoFish writes `MW818422;MW818406`. The
#: first build looked the whole string up as one accession, found nothing,
#: and filed 97,455 12S records (15.7% of the volume) with no name below
#: class; the Sussex Audit found them when a northern rockling present three
#: times came out "Actinopterygii" (decision 0028). A bundle is resolved
#: part by part and given the lineage its parts agree on.
BUNDLE_SEPARATOR = ";"


def split_bundle(accession: str) -> List[str]:
    """The accessions a source header names, one or several."""
    return [part.strip() for part in str(accession).split(BUNDLE_SEPARATOR) if part.strip()]


def agreed_lineage(lineages: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """
    The lineage several records share: every rank from kingdom down on
    which all agree, blank from the first disagreement on. Two accessions
    of one species agree everywhere; a bundle of *Gasterosteus aculeatus*
    with *G. islandicus* is the genus and no species.
    """
    lineages = [l for l in lineages if l]
    if not lineages:
        return None
    agreed: Dict[str, str] = {}
    keep = True
    for column, _ in LINEAGE_COLUMNS:
        values = {(l.get(column) or "").strip() for l in lineages}
        values.discard("")
        if keep and len(values) == 1:
            agreed[column] = values.pop()
        else:
            keep = False
            agreed[column] = ""
    return agreed


def _identified_to_family(lineage: Dict[str, str]) -> bool:
    """
    Whether a reference is identified precisely enough to be worth keeping.

    A sequence whose lineage stops above family cannot support an
    identification at family level or below, which is the coarsest result
    worth reporting. Keeping such records costs search time and disk space
    and can only ever produce an answer that gets discarded later.
    """
    return bool(lineage.get("family") or lineage.get("genus") or lineage.get("species"))


#: How many references to keep for each terrestrial species.
#:
#: A land-class record earns its place by being able to name something that
#: washed in from the catchment, and one good reference names a beetle as well
#: as fifty do. The barcode archive does not agree: BOLD holds 4,451,496
#: land-class COI records across 360,542 species - 12.3 each - and keeping all
#: of them made COI 80% insects and five times its previous size.
#:
#: Two rather than one, because a single reference has no counterweight if it
#: is mislabelled, and the consensus works by comparing references against
#: each other. Nothing marine is capped at any number.
LAND_REFERENCES_PER_SPECIES = 2


def _is_land_class(lineage: Dict[str, str]) -> bool:
    """Whether this record belongs to one of the wholly terrestrial classes."""
    return (lineage.get("class") or "").strip().lower() in EXCLUDED_CLASSES


def _species_key(lineage: Dict[str, str]) -> str:
    """What counts as "the same species" when capping."""
    from src.reference.library import is_unknown

    species = lineage.get("species") or ""
    if not is_unknown(species):
        return species.strip().lower()
    return (lineage.get("genus") or "").strip().lower()


def _named_to_genus(lineage: Dict[str, str]) -> bool:
    """Whether a record can name something at genus level or finer."""
    from src.reference.library import is_unknown

    return not is_unknown(lineage.get("genus")) or not is_unknown(lineage.get("species"))


def _is_marine(lineage: Dict[str, str]) -> bool:
    """
    Whether a lineage is worth keeping in a library built for water samples.

    Judged on phylum first, so that keeping Arthropoda for its crustaceans
    does not also keep every terrestrial insect.

    A land class is then kept only when the record is identified to genus or
    species. Insects, springtails and spiders do wash into water and turn up
    in real samples, and a named one is evidence about the catchment - so
    excluding them outright throws away information a person validating a
    dataset can use. What is dropped is the part that could never say
    anything: a record identified no further than "an insect" cannot name a
    runoff species, and there are 64,872 of those in the COI library alone.

    Nothing outside the land classes is filtered on how well it is identified.
    That was measured and rejected: applying the same rule to everything cut
    11 of the 25 references behind a validated 9/9 recovery and lost four
    English Channel fish entirely. See
    docs/decisions/0010-what-a-marine-library-keeps.md.
    """
    phylum = (lineage.get("phylum") or "").strip().lower()
    if phylum not in MARINE_PHYLA:
        return False
    if (lineage.get("class") or "").strip().lower() in EXCLUDED_CLASSES:
        return _named_to_genus(lineage)
    return True


def add_marker(
    library_root: Path,
    marker: str,
    source_files: List[SourceSpec],
    reporter: Optional[Reporter] = None,
    marine_only: bool = False,
    require_family: bool = False,
    makeblastdb: Optional[Path] = None,
) -> BuildResult:
    """
    Add or replace one marker in a reference library.

    Sources are read in the order given, and earlier ones take precedence:
    where two sources hold the same record, the first wins. That lets a
    curated source act as the authority for the taxa it covers while a
    broader one fills in everything else.
    """
    reporter = reporter or console_reporter()
    library_root = Path(library_root)
    catalogue = library_root / CATALOGUE_NAME
    if not catalogue.exists():
        raise FileNotFoundError(
            f"No reference catalogue at {catalogue}. Create the library first."
        )

    result = BuildResult(marker=marker)
    volume_dir = library_root / VOLUMES_DIR / marker
    volume_dir.mkdir(parents=True, exist_ok=True)
    fasta_path = volume_dir / f"{marker}_sequences.fasta"

    connection = sqlite3.connect(catalogue)
    connection.row_factory = sqlite3.Row
    resolver = TaxonomyResolver(connection, reporter)

    try:
        # Replacing a marker means clearing what was there, so a rebuild does
        # not leave records from a previous source behind.
        existing = connection.execute(
            "SELECT COUNT(*) FROM reference_library WHERE marker_gene = ?", (marker,)
        ).fetchone()[0]
        if existing:
            reporter.info(f"Replacing {existing:,} existing {marker} records.")
            connection.execute("DELETE FROM reference_library WHERE marker_gene = ?", (marker,))
            connection.commit()

        seen: Set[str] = set()
        #: How many references have been kept for each terrestrial
        #: species, so the barcode archive's insects cannot dominate.
        land_per_species: Dict[str, int] = defaultdict(int)
        # Sources overlap heavily - most BOLD barcodes are also deposited in
        # GenBank - so the same sequence arrives twice under two different
        # identifiers. Only the first is kept: a second copy cannot change
        # which species BLAST reports, and merely makes the volume bigger and
        # every search slower. Digests are stored rather than the sequences
        # themselves, which would run to gigabytes.
        seen_sequences: Set[bytes] = set()
        batch: List[Tuple] = []
        counter = 0

        with open(fasta_path, "w", encoding="utf-8") as fasta:
            for spec in source_files:
                if not spec.path.exists():
                    reporter.warning(f"Skipping missing source: {spec.path}")
                    continue

                parser = sources_module.PARSERS.get(spec.source)
                if parser is None:
                    reporter.warning(f"No parser for source '{spec.source}'.")
                    continue

                reporter.info(f"Reading {spec.source} from {spec.path.name}...")
                if spec.note:
                    reporter.info(f"  {spec.note}")
                read = 0
                kept_here = result.written

                for header, sequence in sources_module.iter_fasta(spec.path):
                    read += 1
                    if read % 200000 == 0:
                        reporter.info(f"  {read:,} read, {result.written:,} kept")
                        reporter.checkpoint()

                    if spec.min_length and len(sequence) < spec.min_length:
                        continue
                    if spec.max_length and len(sequence) > spec.max_length:
                        continue

                    record = parser(header, sequence, marker)
                    if record is None or not record.sequence:
                        continue

                    if not record.taxid and spec.taxid_map:
                        # Some sources publish sequences and taxonomy in
                        # separate files; join them back together here - and
                        # a header may bundle several accessions (decision 0028).
                        parts = split_bundle(record.source_accession)
                        taxids = [spec.taxid_map[p] for p in parts if p in spec.taxid_map]
                        if len(taxids) == 1:
                            record.taxid = taxids[0]
                        elif taxids:
                            resolver.load()
                            found = [resolver.lineage_of(t) for t in taxids]
                            record.lineage = agreed_lineage([f for f in found if f]) or record.lineage

                    if record.key in seen:
                        result.skipped_duplicate += 1
                        continue

                    digest = hashlib.sha1(record.sequence.encode("ascii", "ignore")).digest()
                    if digest in seen_sequences:
                        result.skipped_same_sequence += 1
                        continue

                    lineage = resolver.resolve(record)
                    if lineage is None:
                        result.skipped_no_lineage += 1
                        continue
                    if marine_only and not _is_marine(lineage):
                        result.skipped_not_marine += 1
                        continue
                    if marine_only and _is_land_class(lineage):
                        # Enough to name a runoff species, not enough to let
                        # the barcode archive's insects dominate the library.
                        key = _species_key(lineage)
                        if land_per_species[key] >= LAND_REFERENCES_PER_SPECIES:
                            result.skipped_land_surplus += 1
                            continue
                        land_per_species[key] += 1
                    if require_family and not _identified_to_family(lineage):
                        result.skipped_incomplete += 1
                        continue

                    seen.add(record.key)
                    seen_sequences.add(digest)
                    counter += 1
                    identifier = f"{marker}_{counter}"

                    fasta.write(f">{identifier}\n{record.sequence}\n")
                    batch.append((
                        identifier, marker,
                        lineage.get("kingdom", ""), lineage.get("phylum", ""),
                        lineage.get("class", ""), lineage.get("order_rank", ""),
                        lineage.get("family", ""), lineage.get("genus", ""),
                        lineage.get("species", ""),
                        f"{record.source} Acc: {record.source_accession}",
                    ))
                    result.written += 1

                    if len(batch) >= BATCH_SIZE:
                        _flush(connection, batch)
                        batch.clear()

                reporter.info(
                    f"  {spec.source}: {read:,} read, "
                    f"{result.written - kept_here:,} added."
                )

        if batch:
            _flush(connection, batch)
        connection.commit()

    finally:
        connection.close()

    reporter.success(f"{marker}: {result.describe()}")

    if result.written == 0:
        reporter.warning(f"Nothing was written for {marker}; no volume was built.")
        fasta_path.unlink(missing_ok=True)
        return result

    # ------------------------------------------------------------------
    # Compile the searchable volume
    # ------------------------------------------------------------------
    from src.utils.platform import get_bundled_bin, short_path

    makeblastdb = Path(makeblastdb) if makeblastdb else get_bundled_bin("makeblastdb")
    volume = volume_dir / f"local_{marker}_db"
    reporter.info(f"Building the BLAST volume for {marker}...")

    # makeblastdb is run from inside the marker folder, with plain file names
    # rather than full paths. It writes the database happily either way, but
    # its final self-check re-opens the database by name and splits that name
    # on spaces, so a full path containing one fails the check after all the
    # work is done. Bare names inside the folder contain no spaces at all.
    outcome = run_tool(
        [
            makeblastdb,
            "-in", fasta_path.name,
            "-dbtype", "nucl",
            # Without this the volume knows its sequences only by position,
            # reports them all as "BL_ORD_ID:0", and cannot be restricted to
            # a subset - which is what every scope in reference/scopes.py
            # needs. Two of the four volumes in the first library were built
            # without it and had to be rebuilt from themselves afterwards.
            "-parse_seqids",
            "-out", volume.name,
            "-title", f"TaxaTag {marker} reference",
        ],
        log_path=volume_dir / f"makeblastdb_{marker}.log",
        reporter=reporter,
        cwd=short_path(volume_dir),
    )
    if not outcome.ok:
        reporter.error(f"Building the {marker} volume failed: {outcome.tail(3)}")
        return result

    # The FASTA is only an input to makeblastdb; the volume holds the
    # sequences from here on, and keeping both doubles the disk cost.
    fasta_path.unlink(missing_ok=True)
    result.volume = volume
    reporter.success(f"{marker} volume ready: {result.written:,} sequences.")
    return result


def _flush(connection: sqlite3.Connection, batch: List[Tuple]) -> None:
    connection.executemany(
        "INSERT OR REPLACE INTO reference_library VALUES (?,?,?,?,?,?,?,?,?,?)", batch
    )
    connection.commit()


def load_accession_taxids(
    parquet_path: Path, reporter: Optional[Reporter] = None
) -> Dict[str, str]:
    """
    Load an accession -> NCBI taxid mapping from a Parquet table.

    MitoFish distributes its sequences and its taxonomy separately, so the two
    have to be joined before its records can be placed in the tree of life.
    """
    reporter = reporter or console_reporter()
    parquet_path = Path(parquet_path)
    if not parquet_path.exists():
        reporter.warning(f"No accession mapping at {parquet_path}")
        return {}

    try:
        import pandas as pd
    except ImportError:
        reporter.warning("pandas is needed to read the accession mapping.")
        return {}

    frame = pd.read_parquet(parquet_path, columns=["accession", "taxon_id"])
    mapping = {
        str(accession).strip(): str(taxid).strip()
        for accession, taxid in zip(frame["accession"], frame["taxon_id"])
        if accession and taxid
    }
    reporter.info(f"Loaded {len(mapping):,} accession-to-taxon mappings.")
    return mapping


#: Every index the catalogue is expected to have. `create_empty_library` makes
#: them; `ensure_indexes` adds the ones an older library lacks. `idx_genus`
#: arrived on 16 September 2026, when building an adjudication sheet for 799
#: calls took three quarters of an hour: `coverage()` asks for every listed
#: congener of every genus called, and without it each ask scanned the
#: 620,115 12S records.
CATALOGUE_INDEXES = (
    ("idx_marker", "reference_library", "marker_gene"),
    ("idx_species", "reference_library", "species"),
    ("idx_genus", "reference_library", "genus"),
    ("idx_matrix_taxid", "ncbi_taxonomy_matrix", "tax_id"),
)


def ensure_indexes(library_root: Path, reporter: Optional[Reporter] = None) -> List[str]:
    """
    Add any catalogue index a newer TaxaTag expects to an existing library,
    and say which were added. Read-write, deliberately: the library is
    opened read-only everywhere else, so this is the one maintenance step
    a user runs on purpose (`python -m src.reference.cli index`), and it
    changes no record - only how fast they are found.
    """
    reporter = reporter or console_reporter()
    catalogue = Path(library_root) / CATALOGUE_NAME
    if not catalogue.exists():
        raise FileNotFoundError(f"No reference catalogue at {catalogue}")
    connection = sqlite3.connect(catalogue)
    try:
        present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        added = []
        for name, table, column in CATALOGUE_INDEXES:
            if name in present:
                continue
            reporter.info(f"Adding {name} on {table}({column}) - this can take a minute on a large library.")
            connection.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({column})")
            connection.commit()
            added.append(name)
    finally:
        connection.close()
    reporter.info("Indexes added: " + (", ".join(added) if added else "none - the library already had them all."))
    return added


def repair_bundled_names(library_root: Path, taxid_map: Dict[str, str], marker: str = "12S",
                         reporter: Optional[Reporter] = None) -> Dict[str, int]:
    """
    Give a name to every record of `marker` that the first build left
    nameless or half-placed because its header bundled several accessions
    (decision 0028). Read-write, deliberately, like `ensure_indexes`: the one
    maintenance step a person runs on purpose. Needs the source's accession
    -> taxid table (`load_accession_taxids`); records whose accessions the
    table does not know are left as they are and counted.

    Returns counts: candidates looked at, renamed, taken to a genus or
    family only (the bundle's parts disagree), left as they were.
    """
    reporter = reporter or console_reporter()
    catalogue = Path(library_root) / CATALOGUE_NAME
    if not catalogue.exists():
        raise FileNotFoundError(f"No reference catalogue at {catalogue}")
    connection = sqlite3.connect(catalogue)
    counts = {"candidates": 0, "renamed": 0, "partly": 0, "unchanged": 0}
    try:
        resolver = TaxonomyResolver(connection, reporter)
        resolver.load()
        # Nameless records, and records a subspecies name left with a placeholder family.
        rows = connection.execute(
            "SELECT accession, common_name, species FROM reference_library WHERE marker_gene = ? AND "
            "(species = 'Unknown Species' OR family = 'Unknown_Family' OR family = '' OR genus = 'Unknown')",
            (marker,),
        ).fetchall()
        counts["candidates"] = len(rows)
        updates = []
        for library_id, common_name, old_species in rows:
            source = common_name.split("Acc:", 1)[1].strip() if "Acc:" in (common_name or "") else ""
            taxids = [taxid_map[p] for p in split_bundle(source) if p in taxid_map]
            lineage = agreed_lineage([resolver.lineage_of(t) for t in taxids]) if taxids else None
            if not lineage or not any(lineage.values()):
                counts["unchanged"] += 1
                continue
            counts["renamed" if lineage.get("species") else "partly"] += 1
            updates.append((*(lineage.get(column, "") for column, _ in LINEAGE_COLUMNS), library_id))
        columns = ", ".join(f"{column} = ?" for column, _ in LINEAGE_COLUMNS)
        connection.executemany(f"UPDATE reference_library SET {columns} WHERE accession = ?", updates)
        connection.commit()
    finally:
        connection.close()
    reporter.info(f"{marker}: {counts['candidates']:,} records looked at; {counts['renamed']:,} named to species, "
                  f"{counts['partly']:,} to a higher rank, {counts['unchanged']:,} left as they were.")
    return counts


def create_empty_library(library_root: Path, reporter: Optional[Reporter] = None) -> Path:
    """Create the catalogue tables for a new, empty reference library."""
    reporter = reporter or console_reporter()
    library_root = Path(library_root)
    library_root.mkdir(parents=True, exist_ok=True)
    catalogue = library_root / CATALOGUE_NAME

    connection = sqlite3.connect(catalogue)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS reference_library (
            accession TEXT PRIMARY KEY,
            marker_gene TEXT,
            kingdom TEXT, phylum TEXT, class TEXT,
            order_rank TEXT, family TEXT, genus TEXT, species TEXT,
            common_name TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_marker ON reference_library(marker_gene);
        CREATE INDEX IF NOT EXISTS idx_species ON reference_library(species);
        CREATE INDEX IF NOT EXISTS idx_genus ON reference_library(genus);

        CREATE TABLE IF NOT EXISTS ncbi_taxonomy_matrix (
            tax_id TEXT PRIMARY KEY,
            kingdom TEXT, phylum TEXT, class TEXT,
            order_rank TEXT, family TEXT, genus TEXT, species TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_matrix_taxid ON ncbi_taxonomy_matrix(tax_id);
        """
    )
    connection.commit()
    connection.close()
    reporter.success(f"Created an empty reference library at {catalogue}")
    return catalogue

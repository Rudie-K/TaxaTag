# src/reference/sources.py
"""
Reading the reference databases TaxaTag builds its library from.

Each source publishes its sequences in its own FASTA header format, and each
names organisms in its own way. A parser here turns one source into a common
record shape; the builder then resolves every record's lineage through NCBI
taxonomy so that sources can be compared and merged.

Sources currently understood:

    MIDORI2     GenBank-derived, quality controlled, updated every couple of
                months. Headers carry both the GenBank accession and the NCBI
                taxid, which makes it the cleanest source to work from.
    MitoFish    Expert-curated fish mitochondrial sequences. Headers are bare
                accessions with no gene annotation, so entries are selected by
                length rather than by name.
    PR2         Curated 18S for micro-eukaryotes, with a semicolon lineage.
    BOLD        The global barcode library. Vast, and correspondingly varied
                in how completely its records are identified.
"""

from __future__ import annotations

import gzip
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple


@dataclass
class SourceRecord:
    """One reference sequence, as read from a source database."""

    source_accession: str        # the originating database's identifier
    sequence: str
    marker: str
    taxid: str = ""              # NCBI taxid, when the source supplies one
    name: str = ""               # organism name, when it does not
    lineage: Dict[str, str] = field(default_factory=dict)
    source: str = ""
    #: What makes this record distinct within its source. Usually the
    #: accession, but one accession can hold two copies of a gene - many
    #: mitochondrial genomes carry a duplicated region - and those are
    #: separate references that both deserve to be searchable.
    key: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            self.key = self.source_accession

    @property
    def length(self) -> int:
        return len(self.sequence)


def iter_fasta(path: Path, member: Optional[str] = None) -> Iterator[Tuple[str, str]]:
    """
    Stream (header, sequence) pairs from a FASTA file.

    Handles plain, gzipped and zipped files, and streams rather than reading
    into memory: these archives run to hundreds of megabytes uncompressed and
    a build should not need a machine with that much memory spare.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            name = member or archive.namelist()[0]
            with archive.open(name) as handle:
                yield from _iter_fasta_lines(
                    line.decode("utf-8", "replace") for line in handle
                )
    elif suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            yield from _iter_fasta_lines(handle)
    else:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            yield from _iter_fasta_lines(handle)


def _iter_fasta_lines(lines) -> Iterator[Tuple[str, str]]:
    header: Optional[str] = None
    chunks: List[str] = []
    for line in lines:
        line = line.rstrip()
        if line.startswith(">"):
            if header is not None:
                yield header, "".join(chunks)
            header = line[1:]
            chunks = []
        elif header is not None and line:
            chunks.append(line)
    if header is not None:
        yield header, "".join(chunks)


# ---------------------------------------------------------------------------
# MIDORI2
# ---------------------------------------------------------------------------

#: MIDORI2 headers look like
#:   MH910097.1.2081.4777###root_1;Eukaryota_2759;...;Paravannella_minima_1443144
#: The part before ### is the GenBank accession followed by the coordinates of
#: the gene within it; after ### is the lineage, each name carrying its taxid.
_MIDORI_ACCESSION = re.compile(r"^([A-Z]{1,4}_?\d+\.\d+)")
_MIDORI_TAXON = re.compile(r"^(.*)_(\d+)$")

#: Rank prefixes MIDORI2 sometimes writes in front of a name.
_MIDORI_RANK_PREFIX = re.compile(
    r"^(?:root|superkingdom|kingdom|phylum|class|order|family|genus|species)_", re.IGNORECASE
)


def parse_midori2(header: str, sequence: str, marker: str) -> Optional[SourceRecord]:
    """Read one MIDORI2 record."""
    if "###" not in header:
        return None
    identifier, lineage_text = header.split("###", 1)

    match = _MIDORI_ACCESSION.match(identifier.strip())
    if not match:
        return None
    accession = match.group(1)

    # The last element of the lineage is the organism itself, and carries the
    # taxid the whole lineage can be looked up from.
    taxid, name = "", ""
    parts = [p for p in lineage_text.strip().split(";") if p]
    if parts:
        leaf = _MIDORI_RANK_PREFIX.sub("", parts[-1])
        taxon = _MIDORI_TAXON.match(leaf)
        if taxon:
            name = taxon.group(1).replace("_", " ").strip()
            taxid = taxon.group(2)
        else:
            name = leaf.replace("_", " ").strip()

    return SourceRecord(
        source_accession=accession,
        sequence=sequence.upper(),
        marker=marker,
        taxid=taxid,
        name=name,
        source="MIDORI2",
        # The coordinates distinguish two copies of the gene within one record.
        key=identifier.strip(),
    )


# ---------------------------------------------------------------------------
# MitoFish
# ---------------------------------------------------------------------------

#: A fish mitochondrial genome is around 16-17 kb. Anything near that length
#: contains every mitochondrial gene, 16S included, so such entries can serve
#: as 16S references without the gene needing to be cut out first: BLAST
#: aligns the query to whichever part of the genome it belongs to.
MITOGENOME_MIN_LENGTH = 10000


def parse_mitofish(header: str, sequence: str, marker: str) -> Optional[SourceRecord]:
    """
    Read one MitoFish record.

    MitoFish headers are a bare accession with no indication of which gene the
    sequence holds, so callers select entries by length instead.
    """
    accession = header.strip().split()[0].lstrip(">").strip()
    if not accession:
        return None
    return SourceRecord(
        source_accession=accession,
        sequence=sequence.upper(),
        marker=marker,
        source="MitoFish",
    )


# ---------------------------------------------------------------------------
# PR2
# ---------------------------------------------------------------------------

def parse_pr2(header: str, sequence: str, marker: str) -> Optional[SourceRecord]:
    """
    Read one PR2 record.

    PR2 headers are an accession followed by a semicolon-separated lineage,
    with no taxids, so the organism is matched to NCBI by name.
    """
    parts = header.strip().split("|", 1)
    accession = parts[0].split()[0].strip()
    lineage_text = parts[1] if len(parts) > 1 else ""
    names = [p.strip() for p in lineage_text.split(";") if p.strip()]
    return SourceRecord(
        source_accession=accession,
        sequence=sequence.upper(),
        marker=marker,
        name=names[-1] if names else "",
        source="PR2",
    )


# ---------------------------------------------------------------------------
# BOLD
# ---------------------------------------------------------------------------

#: A BOLD header is pipe-separated:
#:   AANIC003-10|COI-5P|Australia|Animalia,Arthropoda,Insecta,Lepidoptera,...
#: process id | marker code | country | comma-separated lineage.
#:
#: The lineage runs kingdom, phylum, class, order, family, subfamily, genus,
#: species. Subfamily sits between family and genus and has no column in the
#: catalogue, so it is read and dropped rather than shifting everything below
#: it up a rank.
_BOLD_LINEAGE_RANKS = [
    "kingdom", "phylum", "class", "order_rank", "family", None, "genus", "species",
]

#: BOLD writes this where a rank is not known.
_BOLD_MISSING = {"", "none", "null", "n/a"}


def parse_bold(header: str, sequence: str, marker: str) -> Optional[SourceRecord]:
    """
    Read one BOLD record.

    BOLD carries its own complete lineage, which is used directly. That
    matters for the marine filter, which needs the phylum: resolving BOLD's
    names against NCBI one at a time would be both slower and less complete,
    since many barcoded taxa have no NCBI entry at all.
    """
    fields = [f.strip() for f in header.split("|")]
    if not fields or not fields[0]:
        return None

    # The public archive is COI, but it carries the marker code anyway, so
    # anything that is not the barcoding region is left out.
    marker_code = fields[1] if len(fields) > 1 else ""
    if marker_code and not marker_code.upper().startswith("COI"):
        return None

    lineage: Dict[str, str] = {}
    if len(fields) > 3:
        names = [n.strip() for n in fields[3].split(",")]
        for rank, value in zip(_BOLD_LINEAGE_RANKS, names):
            if rank and value and value.lower() not in _BOLD_MISSING:
                lineage[rank] = value

    return SourceRecord(
        source_accession=fields[0],
        sequence=sequence.upper(),
        marker=marker,
        name=lineage.get("species", "") or lineage.get("genus", ""),
        lineage=lineage,
        source="BOLD",
    )


#: Parsers by source name.
PARSERS: Dict[str, Callable[[str, str, str], Optional[SourceRecord]]] = {
    "MIDORI2": parse_midori2,
    "MitoFish": parse_mitofish,
    "PR2": parse_pr2,
    "BOLD": parse_bold,
}

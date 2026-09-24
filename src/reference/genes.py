# src/reference/genes.py
"""
Which gene each reference record holds, from its source's own annotation.

The marine core's 12S volume is MitoFish's whole partial-sequence set: every
fish mitochondrial sequence in GenBank, filed under 12S (planned item 9).
Decision 0009 rightly declined to filter it by sequence content - BLAST
needs no filter - but decisions 0026 and 0031 made record counts into
evidence, and a count of "12S references" was counting cytochrome b, COI
and control region records too: about fifteen times too many (the Sussex
Audit's issue 46).

So each record learns its genes, by annotation, not by sequence:

    MitoFish's gene table     `seq_annotation.parquet`, shipped beside its
                              FASTA: every gene annotated on each accession,
                              with coordinates.
    the GenBank title         `seq_description.parquet`: catches what the
                              table leaves out - the control region is not
                              among its genes - and names a whole genome.

Only the catalogue learns; the BLAST volume is unchanged, because a COI
record in the 12S volume is harmless to a search. The title also carries
the words Collins 2021 and Bourret 2023 exclude on - "UNVERIFIED",
"PREDICTED", "similar to", "-like" - so they are kept as flags.

Checked on 24 September 2026 against `src/analysis/amplicons.py`, which
finds the MiFish amplicon by primers and alignment, independently: of the
35,912 records carrying the amplicon, 35,886 hold 12S by annotation or
title, and 26 do not; 53,275 of the volume's 620,115 records hold 12S
(8.6%), as the Sussex Audit's count from GenBank titles found (decision
0041).
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, Optional, Set, Tuple

#: The gene vocabulary the catalogue uses. A record can hold several.
GENE_12S, GENE_16S, GENE_COI, GENE_CYTB = "12S", "16S", "COI", "cytb"
GENE_CONTROL_REGION, GENE_MITOGENOME, GENE_OTHER = "control region", "mitogenome", "other"
GENES = (GENE_12S, GENE_16S, GENE_COI, GENE_CYTB, GENE_CONTROL_REGION, GENE_MITOGENOME, GENE_OTHER)

#: Which gene a marker volume is for. 18S is nuclear and has no MitoFish
#: records; its volume is PR2's SSU set, 18S by construction.
MARKER_GENE = {"12S": GENE_12S, "16S": GENE_16S, "COI": GENE_COI, "18S": "18S"}

#: MitoFish's gene names, to the vocabulary. Anything not here is "other".
_ANNOTATION = {"12S_rRNA": GENE_12S, "16S_rRNA": GENE_16S, "COXI": GENE_COI, "Cytb": GENE_CYTB}

#: What a GenBank title says, to the vocabulary. Patterns are tried on the
#: whole title; the cytochrome oxidase one refuses subunits II and III.
_TITLE_PATTERNS = (
    (re.compile(r"\b12S\b|small subunit ribosomal RNA|s-rRNA", re.I), GENE_12S),
    (re.compile(r"\b16S\b|large subunit ribosomal RNA|l-rRNA", re.I), GENE_16S),
    (re.compile(r"\bCOX?1\b|\bCOI\b|cytochrome (c )?oxidase subunit (1|I)\b(?!I)", re.I), GENE_COI),
    (re.compile(r"\bcytb\b|\bcyt-?b\b|cytochrome b\b", re.I), GENE_CYTB),
    (re.compile(r"control region|D-loop|\bdloop\b", re.I), GENE_CONTROL_REGION),
    (re.compile(r"mitochondri(on|al DNA), complete genome|complete mitochondrial genome", re.I), GENE_MITOGENOME),
)

#: The words a record abstains by, in Collins 2021 and Bourret 2023.
FLAG_PATTERNS = (
    ("UNVERIFIED", re.compile(r"\bUNVERIFIED\b")),
    ("PREDICTED", re.compile(r"\bPREDICTED\b")),
    ("similar to", re.compile(r"\bsimilar to\b", re.I)),
    ("-like", re.compile(r"\w-like\b", re.I)),
)


def from_annotation(names: Iterable[str]) -> Set[str]:
    """MitoFish's gene names for one accession, in the vocabulary."""
    return {_ANNOTATION.get(name, GENE_OTHER) for name in names if name}


def from_title(title: str) -> Set[str]:
    return {gene for pattern, gene in _TITLE_PATTERNS if pattern.search(title or "")}


def flags_in(title: str) -> Set[str]:
    return {flag for flag, pattern in FLAG_PATTERNS if pattern.search(title or "")}


def genes_of(annotated: Iterable[str], title: str, length: int, mitogenome_min_length: int) -> Set[str]:
    """
    Everything a record holds. A whole mitochondrial genome holds every
    gene, so it is named as one rather than listed gene by gene; its
    length decides as well as its title, as the build's own selection of
    mitogenomes does (`sources.MITOGENOME_MIN_LENGTH`).
    """
    genes = from_annotation(annotated) | from_title(title)
    if length >= mitogenome_min_length:
        genes.add(GENE_MITOGENOME)
    if not genes and (title or "").strip():
        # A title naming none of the marker genes - a tRNA, rps7, a repeat
        # unit - is some other gene. With no title and no annotation the
        # record stays unknown, which is not the same thing.
        genes.add(GENE_OTHER)
    return genes


def holds(genes: Set[str], marker: str) -> bool:
    """Whether a record with these genes is a reference *of* `marker`."""
    wanted = MARKER_GENE.get(marker, marker)
    return wanted in genes or GENE_MITOGENOME in genes


def encode(values: Iterable[str]) -> str:
    """As the catalogue stores a set: in the vocabulary's order, joined by |."""
    values = set(values)
    order = list(GENES) + sorted(values - set(GENES))
    return "|".join(v for v in order if v in values)


def decode(text: Optional[str]) -> Set[str]:
    return {part for part in (text or "").split("|") if part}


def load_mitofish(annotation_parquet, description_parquet) -> Tuple[Dict[str, Set[str]], Dict[str, str]]:
    """
    MitoFish's two tables: each accession's annotated gene names, and its
    GenBank title. Accessions are read without their version, as the
    catalogue's bundled source accessions are.
    """
    import pandas as pd

    annotated: Dict[str, Set[str]] = {}
    table = pd.read_parquet(annotation_parquet, columns=["accession", "gene"])
    for accession, gene in zip(table["accession"], table["gene"]):
        annotated.setdefault(str(accession).split(".")[0], set()).add(str(gene))
    descriptions = pd.read_parquet(description_parquet, columns=["accession", "description"])
    titles = {str(a).split(".")[0]: str(d or "") for a, d in zip(descriptions["accession"], descriptions["description"])}
    return annotated, titles

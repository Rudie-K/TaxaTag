# src/utils/accessions.py
"""
Reading database accession numbers out of BLAST output.

BLAST reports the sequence it matched in whichever form the database was
built with. A plain FASTA database gives a bare accession; one built with
sequence-id parsing gives `gb|MZ100001.1|`; NCBI's own databases have used
several styles over the years. Everything downstream wants the bare versioned
accession, so the tidying happens once, here.
"""

from __future__ import annotations

import re

#: A versioned INSDC accession: one to four letters, digits, then .version.
#: The optional underscore covers RefSeq identifiers such as NC_012920.1.
ACCESSION_PATTERN = re.compile(r"\b([A-Z]{1,4}_?\d{4,}\.\d+)\b")

#: What BLAST writes when a database carries no taxonomy information. Taken
#: literally it would become a species called "N/A".
BLAST_PLACEHOLDERS = {"", "N/A", "NA", "0", "-", "none", "unknown"}


def normalise_accession(value: str) -> str:
    """
    Reduce a BLAST subject id to a bare versioned accession.

    Falls back to the original text when nothing accession-shaped is present,
    so an unusual database still produces something identifiable rather than
    an empty column.
    """
    if not value:
        return ""
    text = str(value).strip()
    match = ACCESSION_PATTERN.search(text)
    if match:
        return match.group(1)
    # A pipe-delimited id with no recognisable accession: take the longest
    # field, which is the identifier rather than the database tag.
    if "|" in text:
        parts = [part for part in text.split("|") if part]
        if parts:
            return max(parts, key=len)
    return text


def is_placeholder(value: str) -> bool:
    """True when a BLAST field holds a 'nothing here' marker rather than data."""
    return str(value).strip().lower() in {p.lower() for p in BLAST_PLACEHOLDERS}

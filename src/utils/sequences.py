# src/utils/sequences.py
"""
DNA sequence helpers.

Small enough to keep in one place, and worth keeping out of the interface code
so they can be tested on their own.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Complement of every IUPAC nucleotide code, in both cases.
#:
#: Primers routinely contain ambiguity codes - MarVer3's forward primer
#: AGACGAGAAGACCCTRTG has an R, meaning A or G - so a complement table
#: covering only ACGT would silently corrupt them.
_COMPLEMENT = str.maketrans(
    "ACGTURYSWKMBDHVNacgturyswkmbdhvn",
    "TGCAAYRSWMKVHDBNtgcaayrswmkvhdbn",
)

#: Every character that may legitimately appear in a primer.
VALID_BASES = set("ACGTURYSWKMBDHVN")


def read_lengths(path, limit: int = 2000) -> list:
    """
    The lengths of the first reads in a FASTQ file, plain or gzipped.

    Enough of the file to describe it, not all of it: a sample carries
    millions of reads and the question being asked - how long is what came out
    of trimming - is answered just as well by the first few thousand. Returns
    an empty list rather than raising, because a file that cannot be read is
    something the caller should report, not crash on.
    """
    import gzip

    path = Path(path)
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    lengths = []
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle):
                if len(lengths) >= limit:
                    break
                if number % 4 == 1:          # the sequence lines
                    lengths.append(len(line.strip()))
    except OSError:
        return []
    return lengths


def reverse_complement(sequence: str) -> str:
    """
    The reverse complement of a DNA sequence.

    This is the sequence as it reads on the opposite strand, which is how a
    primer appears when the sequencer has read through to the far end of the
    amplicon. Ambiguity codes are complemented correctly (R, meaning A or G,
    becomes Y, meaning C or T).
    """
    return sequence.strip().translate(_COMPLEMENT)[::-1]


#: What each IUPAC code can stand for, as a regular expression fragment.
#: Metabarcoding primers are deliberately degenerate so that one pair
#: amplifies many species, so a primer that is pure A/C/G/T is the exception
#: rather than the rule.
_IUPAC_PATTERN = {
    "A": "A", "C": "C", "G": "G", "T": "T", "U": "T",
    "R": "[AG]", "Y": "[CT]", "S": "[GC]", "W": "[AT]",
    "K": "[GT]", "M": "[AC]",
    "B": "[CGT]", "D": "[AGT]", "H": "[ACT]", "V": "[ACG]",
    "N": "[ACGT]",
    # Inosine pairs with any base. Some published primers are written with it,
    # notably jgHCO2198 for COI.
    "I": "[ACGT]",
}


def primer_pattern(primer: str) -> "re.Pattern":
    """
    A compiled regular expression that matches a primer in a read.

    Matching a degenerate primer as a plain string cannot work: the literal
    characters W, Y and N never appear in sequencing output, so
    "GGWACWGGWTGA" matches nothing at all. Every position has to be expanded
    to the set of bases it stands for.
    """
    cleaned = clean_primer(primer)
    return re.compile("".join(_IUPAC_PATTERN.get(base, "[ACGT]") for base in cleaned))


def clean_primer(sequence: str) -> str:
    """Normalise a primer typed or pasted by a user."""
    return "".join(sequence.split()).upper()


#: One base each ambiguity code stands for, used to write down a sequence a
#: degenerate primer could have come from. Which one is arbitrary; what
#: matters is that it is a real sequence of A, C, G and T.
_ONE_OF = {
    "R": "A", "Y": "C", "S": "G", "W": "A", "K": "G", "M": "A",
    "B": "C", "D": "A", "H": "A", "V": "A", "N": "A", "I": "A", "U": "T",
}


def example_sequence(primer: str) -> str:
    """
    One concrete sequence a degenerate primer could have amplified.

    Useful for asking whether two primers can match the same read: a pattern
    cannot be tested against another pattern, but it can be tested against an
    example of what the other one accepts.
    """
    return "".join(_ONE_OF.get(base, base) for base in clean_primer(primer))


def invalid_bases(sequence: str) -> str:
    """
    Any characters in a primer that are not valid nucleotide codes.

    Returns them in the order first seen, so a message can name them. An empty
    string means the primer is fine.
    """
    seen: list[str] = []
    for base in clean_primer(sequence):
        if base not in VALID_BASES and base not in seen:
            seen.append(base)
    return "".join(seen)

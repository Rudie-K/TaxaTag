# src/reference/markers.py
"""
Marker genes, and which reference data belongs to each.

A locus in the settings is a primer pair with a name the user chose, such as
"MiFish_12S". A marker is the gene those primers amplify, such as "12S". Many
primer sets target the same marker - MiFish, Teleo and Riaz all amplify 12S -
so reference sequences are organised by marker rather than by primer set, and
several loci can share one reference volume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Marker:
    """One marker gene and the reference data compiled for it."""

    key: str            # the folder and database name, e.g. "12S"
    label: str          # how it is described to a user
    description: str
    typical_targets: str

    @property
    def volume_name(self) -> str:
        """The BLAST database name inside the marker's folder."""
        return f"local_{self.key}_db"


#: The markers TaxaTag can hold reference data for.
MARKERS: Dict[str, Marker] = {
    "12S": Marker(
        key="12S",
        label="12S rRNA",
        description="Mitochondrial 12S, the standard fish barcoding region.",
        typical_targets="Fish. Used by the MiFish, Teleo and Riaz primer sets.",
    ),
    "16S": Marker(
        key="16S",
        label="16S rRNA",
        description="Mitochondrial 16S, a longer vertebrate barcoding region.",
        typical_targets="Marine vertebrates including mammals. Used by MarVer3.",
    ),
    "COI": Marker(
        key="COI",
        label="COI",
        description="Cytochrome oxidase I, the animal barcoding standard.",
        typical_targets="Invertebrates. Used by the Leray and Folmer primer sets.",
    ),
    "18S": Marker(
        key="18S",
        label="18S rRNA",
        description="Nuclear small-subunit rRNA, for micro-eukaryotes.",
        typical_targets="Plankton, algae, protists and kelp.",
    ),
}

#: The order markers are tried in. A name that mentions two of them resolves
#: to the first, so the more specific come first.
_MARKER_ORDER = ("12S", "16S", "18S", "COI")

#: Published primer sets, by the marker they amplify.
#:
#: These are whole names rather than gene tokens, so they need no boundary
#: care - nothing except a 12S primer set is called "mifish". They are listed
#: so that someone who names their primer set after the paper it came from
#: does not also have to state the marker.
_KNOWN_SETS: Dict[str, Tuple[str, ...]] = {
    "12S": ("mifish", "mimammal", "mibird", "teleo", "tele02", "riaz", "elas02"),
    "16S": ("marver", "vert16"),
    "18S": ("euka", "tareuk", "uni18s"),
    "COI": ("leray", "folmer", "mlcoi", "jghco", "lco1490", "hco2198", "fwhf2"),
}

#: The gene's own name as it can appear inside a locus name, with how tightly
#: the character in front of it must be checked.
#:
#: The character *after* a token is always checked the same way: a letter may
#: not follow it. Without that, "Plate12Sample" reads as 12S and "coil" reads
#: as COI - and the consequence is not a wrong label but a primer set searched
#: against the wrong reference volume, which produces a plausible, empty
#: result. A digit or a separator may follow, so "18S_V4" still resolves.
#:
#: The character in front is the part that varies, and is the second value in
#: each pair:
#:
#:   True  - a letter may run straight into the token, as in "Ceph18S" or
#:           "EukSSU". Right for a token distinctive enough that letters in
#:           front of it are almost certainly a prefix someone chose.
#:   False - a separator is required, as in "Folmer_CO1". Right for a token
#:           short enough to land inside an ordinary word: "Marco1" is not a
#:           COI primer set.
#:
#: A digit may never precede a token either way, so a plate or run number
#: cannot become a marker.
_MARKER_TOKENS: Dict[str, Tuple[Tuple[str, bool], ...]] = {
    # Two digits and an S. Distinctive enough that letters in front of it are
    # a prefix someone chose - "Ceph18S", "Ac12S", "Vert16S" - and the
    # trailing rule is what stops "Plate12Sample".
    "12S": (("12s", True),),
    "16S": (("16s", True),),
    # No English word ends in "ssu", so a letter in front is safe: "EukSSU"
    # resolves, while "tissue" and "assure" are stopped by the letter after.
    "18S": (("18s", True), ("ssu", True)),
    "COI": (
        # "coil", "coin" and "coincide" are all stopped by the trailing rule,
        # which leaves nothing that ends in "coi" but a primer set.
        ("coi", True),
        # Four characters, and the only thing spelt this way is the gene.
        ("cox1", True),
        # Two common letters and a digit. This is the one that has to be
        # strict in front as well: allowing a letter would make "Marco1" a COI
        # primer set. "Folmer_CO1" still resolves, because the underscore is
        # read as a separator.
        ("co1", False),
    ),
}


def _token_pattern(token: str, letter_may_precede: bool) -> str:
    """One marker token as a regex fragment, with its boundaries applied."""
    before = r"(?<![0-9])" if letter_may_precede else r"(?<![0-9a-z])"
    return before + re.escape(token) + r"(?![a-z])"


def _compile_name_patterns():
    """One pattern per marker, built from its tokens and its known sets."""
    compiled = []
    for marker_key in _MARKER_ORDER:
        parts = [
            _token_pattern(token, loose)
            for token, loose in _MARKER_TOKENS.get(marker_key, ())
        ]
        parts += [re.escape(name) for name in _KNOWN_SETS.get(marker_key, ())]
        if parts:
            compiled.append((marker_key, re.compile("|".join(parts), re.IGNORECASE)))
    return compiled


#: Patterns that identify a marker from a locus name, tried in order.
_NAME_PATTERNS = _compile_name_patterns()


def infer_marker(locus_name: str) -> Optional[str]:
    """
    Work out which marker a locus targets from its name.

    Saves the user from having to state something that "MiFish_12S" already
    says. An explicit `marker` in the settings always takes precedence.
    """
    if not locus_name:
        return None
    # Underscores, hyphens and dots are word separators here, so that
    # "MiFish_12S" and "MiFish.12S" read the same way.
    normalised = re.sub(r"[_\-.]+", " ", locus_name)
    for marker_key, pattern in _NAME_PATTERNS:
        if pattern.search(normalised):
            return marker_key
    return None


def marker_for_locus(locus: Dict) -> Optional[str]:
    """The marker a configured locus belongs to, explicit or inferred."""
    explicit = (locus.get("marker") or "").strip()
    if explicit:
        # Accept any capitalisation the user typed.
        for key in MARKERS:
            if explicit.upper() == key.upper():
                return key
        return explicit
    return infer_marker(locus.get("name", ""))


def describe(marker_key: str) -> str:
    """A one-line description of a marker, for the interface."""
    marker = MARKERS.get(marker_key)
    if marker is None:
        return marker_key
    return f"{marker.label} - {marker.typical_targets}"


def known_markers() -> List[str]:
    return list(MARKERS)

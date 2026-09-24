# src/analysis/coverage.py
"""
Which species on a list this library can name at all, per marker.

    python -m src.analysis coverage --library <folder> --species-list <csv> [--out <csv>]

A reference library answers "what is this sequence" only for species it
holds a sequence of. Before a survey's names are trusted, the question to
ask is the other way round: of the species that could have been there,
which ones has the library heard of? Claver et al. (2021, 2023) asked it
of GenBank against the European marine fish checklist - roughly half the
species had any 12S or 16S sequence, and fewer covered the amplicon - and
found the answer explained most of what a survey could and could not
name. `planned.md` item 4, tier 1.

One row per listed species per marker, with the count of references for
the species (under its accepted name or any synonym the list gives), the
count for its genus, and how many species of the genus the library holds.
The grade is Bourret et al.'s, applied to the list rather than to a call:

    named      the library holds a reference for the species itself
    genus      none for the species, but for another species of the genus -
               a sequence of this species would be named to the genus, or to
               the congener, and nothing in the result would say which
    absent     nothing for the genus either

This is the library's side of the coverage columns on the adjudication
sheet, and it needs no run: a list and a library. Whether a reference
*covers the amplicon* is a different question (planned item 6), which a
count cannot answer.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Iterable, List

from src.analysis.adjudication import SpeciesList

COLUMNS = ["Marker", "Species", "Matched_As", "Genus", "Species_References", "Genus_References", "Genus_Species", "Grade",
           "Counted"]

#: What a count counted. A catalogue that records each record's gene
#: (decision 0041) counts records *of the marker's gene*; an older one counts
#: every record of the volume, which on the marine core's 12S volume
#: overstated the listed fishes' references twelve-fold (planned item 9).
COUNTED_BY_GENE, COUNTED_WHOLE_VOLUME = "records of the gene", "every record in the volume"
GRADE_NAMED, GRADE_GENUS, GRADE_ABSENT = "named", "genus", "absent"


def coverage_rows(library, species_list: SpeciesList, markers: Iterable[str]) -> List[Dict[str, str]]:
    """
    `library` needs `coverage(marker, species, genus)`, which `ReferenceLibrary`
    has. A species is looked up under its accepted name first and then under
    each synonym the list carries; the first that the library knows wins,
    and `Matched_As` records which it was, so a name the library keeps under
    an older spelling is a `named` row that says so rather than an `absent`
    one that lies.
    """
    aliases: Dict[str, List[str]] = {name: [name] for name in species_list.accepted}
    for synonym, accepted in species_list.synonyms.items():
        aliases.setdefault(accepted, [accepted]).append(synonym)
    rows: List[Dict[str, str]] = []
    for marker in markers:
        for accepted in sorted(aliases):
            genus = accepted.split(" ")[0]
            matched, cover = "", {"species_references": 0, "genus_references": 0, "genus_species": 0}
            for alias in aliases[accepted]:
                found = library.coverage(marker, alias, alias.split(" ")[0])
                if found.get("species_references", 0):
                    matched, cover = alias, found
                    break
                if not cover.get("genus_references") and found.get("genus_references"):
                    cover = found
            if cover["species_references"]:
                grade = GRADE_NAMED
            elif cover["genus_references"]:
                grade = GRADE_GENUS
            else:
                grade = GRADE_ABSENT
            rows.append({
                "Marker": marker, "Species": accepted, "Matched_As": matched if matched != accepted else "",
                "Genus": genus, "Species_References": str(cover["species_references"]),
                "Genus_References": str(cover["genus_references"]), "Genus_Species": str(cover["genus_species"]),
                "Grade": grade,
                "Counted": COUNTED_BY_GENE if cover.get("gene_aware") or library_knows_genes(library) else COUNTED_WHOLE_VOLUME,
            })
    return rows


def library_knows_genes(library) -> bool:
    knows = getattr(library, "knows_genes", None)
    return bool(knows()) if callable(knows) else False


def summarise(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, int]]:
    """marker -> {named, genus, absent, total}."""
    out: Dict[str, Dict[str, int]] = {}
    for row in rows:
        counts = out.setdefault(row["Marker"], {GRADE_NAMED: 0, GRADE_GENUS: 0, GRADE_ABSENT: 0, "total": 0})
        counts[row["Grade"]] += 1
        counts["total"] += 1
    return out


def write_coverage(library, species_list: SpeciesList, markers: Iterable[str], out: Path) -> List[Dict[str, str]]:
    rows = coverage_rows(library, species_list, markers)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return rows

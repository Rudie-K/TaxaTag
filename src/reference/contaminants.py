# src/reference/contaminants.py
"""
Taxa that are almost always contamination rather than a finding.

A water sample sequenced for fish will routinely return human DNA, and often
cow, pig, chicken or dog. These come from the person who took the sample, from
the laboratory, from agricultural runoff and from what someone had for lunch.
Reporting them alongside the fish is not wrong - they really are in the tube -
but they are not what anybody is looking for, and in this dataset they were
loud: nearly 300,000 reads across seven taxa, with Homo sapiens alone in 20 of
109 samples.

Two rules govern what is on this list, and they are what keep it defensible:

* **A name here is hidden, never deleted.** The species table on disk always
  holds everything. This decides what a panel shows by default and what an
  export may leave out, and both say so.
* **A taxon is only listed when its presence in an aquatic sample is far more
  easily explained by contamination than by biology.** That is a high bar, and
  it deliberately excludes animals that could genuinely be there.

The exclusions matter more than the inclusions. Rats and mice are not here:
Rattus norvegicus lives on riverbanks and in harbours, and the NatureMetrics
report for this very survey lists it as a detection. Deer, foxes, badgers and
livestock that drink from watercourses are not here either, beyond the farmed
species above - a cow in a catchment sample can be real ecological signal about
land use. Nothing marine is here, and nothing that has ever been a survey
target is here.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional

#: Genus, and why it is listed. The reason is carried with the name so that
#: anyone reviewing this list can judge each entry rather than trusting it.
CONTAMINANT_GENERA: Dict[str, str] = {
    "homo": "the person who took, handled or sequenced the sample",
    "bos": "cattle - food, agricultural runoff, and a common laboratory reagent source",
    "sus": "pig - food and agricultural runoff",
    "ovis": "sheep - food and agricultural runoff",
    "capra": "goat - food and agricultural runoff",
    "gallus": "chicken - food, and the most common poultry contaminant",
    "meleagris": "turkey - food",
    "canis": "dog - handling and surface runoff",
    "felis": "cat - handling and surface runoff",
}

#: The species usually meant by each genus above, for a clearer explanation.
COMMON_NAMES: Dict[str, str] = {
    "homo": "human",
    "bos": "cattle",
    "sus": "pig",
    "ovis": "sheep",
    "capra": "goat",
    "gallus": "chicken",
    "meleagris": "turkey",
    "canis": "dog",
    "felis": "cat",
}


def genus_of(name: str) -> str:
    """The genus part of a scientific name, lowercased."""
    text = str(name or "").strip()
    return text.split()[0].lower() if text else ""


def is_likely_contaminant(
    scientific_name: str = "", genus: str = "", family: str = ""
) -> bool:
    """
    Whether a detection is one of the named contaminants.

    Matched on genus rather than on species, because a call may be reported at
    genus level - "Canis" rather than "Canis lupus" - and both mean the same
    thing here. Family is accepted but not used to decide: Bovidae holds wild
    antelope as well as cattle, and Felidae holds wildcats, so widening the
    match to family would start hiding animals that could genuinely be present.
    """
    candidate = genus_of(genus) or genus_of(scientific_name)
    return candidate in CONTAMINANT_GENERA


def reason(scientific_name: str = "", genus: str = "") -> str:
    """Why a detection is treated as contamination, in a few words."""
    candidate = genus_of(genus) or genus_of(scientific_name)
    return CONTAMINANT_GENERA.get(candidate, "")


def summarise(names: Iterable[str]) -> str:
    """
    Describe which contaminants appear in a set of names, for a dialog.

    Named individually rather than counted, because "3 taxa" tells a user
    nothing about whether hiding them is the right choice and "human, cattle,
    dog" tells them everything.
    """
    found = []
    for name in names:
        common = COMMON_NAMES.get(genus_of(name))
        if common and common not in found:
            found.append(common)
    if not found:
        return ""
    if len(found) == 1:
        return found[0]
    return ", ".join(found[:-1]) + " and " + found[-1]


def split(rows: Iterable[list], name_column: Optional[int], genus_column: Optional[int]):
    """
    Separate rows into those to keep and those treated as contamination.

    Returns (kept, hidden). A table with no name column is returned untouched,
    because guessing which column held the species would be worse than not
    filtering at all.
    """
    rows = list(rows)
    if name_column is None and genus_column is None:
        return rows, []

    kept, hidden = [], []
    for row in rows:
        name = row[name_column] if name_column is not None and len(row) > name_column else ""
        genus = row[genus_column] if genus_column is not None and len(row) > genus_column else ""
        (hidden if is_likely_contaminant(name, genus) else kept).append(row)
    return kept, hidden

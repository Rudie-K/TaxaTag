# src/validation/datasets.py
"""
Published sequencing runs whose true content is known.

A pipeline that runs without crashing is not the same as a pipeline that gets
the right answer. These are public datasets where the species present were
decided by the people who made them - mock communities assembled from named
cultures, or samples taken from a known set of animals - so what TaxaTag
reports can be compared against what is actually there.

Each marker TaxaTag supports has at least one dataset here, because a mistake
in a primer set, a reference volume or a threshold usually affects one marker
and leaves the others working perfectly.

The expected lists are the taxa the authors say are present. A detection
outside that list is not automatically an error: environmental samples carry
whatever was in the water, and even a mock community carries the culture
medium's own passengers. The counts are a guide to whether something has
broken, not a score to be maximised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ValidationDataset:
    """One published dataset with a known answer."""

    key: str                     # short name used on the command line
    marker: str                  # the marker TaxaTag should assign it to
    locus: str                   # which primer set to analyse it with
    bioproject: str
    runs: List[str]
    expected: List[str]          # taxa the authors report as present
    description: str
    citation: str
    #: Taxa that are known to be in the data but are not the point of the
    #: experiment - a host, a culture contaminant, a spike-in.
    tolerated: List[str] = field(default_factory=list)
    #: Roughly how much data a single run holds, so a user can judge the wait.
    notes: str = ""

    @property
    def expected_genera(self) -> List[str]:
        """The genus of each expected taxon, for a coarser comparison."""
        return sorted({name.split()[0] for name in self.expected if name})


#: Every dataset TaxaTag is checked against, keyed by short name.
DATASETS: Dict[str, ValidationDataset] = {
    "18S-phytoplankton": ValidationDataset(
        key="18S-phytoplankton",
        marker="18S",
        locus="18S_V4",
        bioproject="PRJNA956448",
        # Ten of the mock communities, plus the negative control, which should
        # come back empty and is the check that the pipeline is not inventing
        # things out of noise.
        runs=[
            "SRR24210251", "SRR24210252", "SRR24210253", "SRR24210254",
            "SRR24210255", "SRR24210256", "SRR24210257", "SRR24210258",
        ],
        expected=[
            "Alexandrium minutum",
            "Alexandrium pacificum",
            "Scrippsiella",
            "Chaetoceros socialis",
            "Skeletonema marinoi",
            "Pseudo-nitzschia",
            "Thalassionema frauenfeldii",
        ],
        description=(
            "Marine phytoplankton mock communities: seven cultured diatom and "
            "dinoflagellate species, combined in known proportions by counting "
            "cells. Includes a negative control (run SRR24210258, 'NC')."
        ),
        citation=(
            "Mock community experiments can inform on the reliability of eDNA "
            "metabarcoding data: a case study on marine phytoplankton. "
            "Scientific Reports 13 (2023). doi:10.1038/s41598-023-47462-5"
        ),
        notes=(
            "18S V4 with the TAReuk primers. The reads are deposited with R1 "
            "and R2 the other way round from the usual convention, which makes "
            "this a useful check that read orientation is handled."
        ),
    ),
    "COI-leray": ValidationDataset(
        key="COI-leray",
        marker="COI",
        locus="COI_Leray",
        bioproject="PRJNA899333",
        runs=["SRR29409451", "SRR22284826"],
        expected=[
            "Hexagrammos octogrammus",
            "Pholidapus dybowskii",
            "Pandalus latirostris",
        ],
        description=(
            "Aquarium water holding three known marine animals from Peter the "
            "Great Gulf, Japan Sea: two fish and one shrimp. A small and "
            "unambiguous test - if these three are not recovered, something is "
            "wrong."
        ),
        citation=(
            "Experimental evaluation of genetic variability based on DNA "
            "metabarcoding from the aquatic environment: insights from the "
            "Leray COI fragment. PMC11222756."
        ),
        notes="COI with the standard Leray mlCOIintF / jgHCO2198 primers.",
    ),
    "12S-16S-coastal": ValidationDataset(
        key="12S-16S-coastal",
        marker="12S",
        locus="MiFish_12S",
        bioproject="PRJNA1090011",
        runs=["SRR28499800", "SRR28499820"],
        expected=[
            "Spondyliosoma cantharus",
            "Sardina pilchardus",
            "Sprattus sprattus",
            "Scomber scombrus",
            "Dicentrarchus labrax",
            "Gobius paganellus",
            "Labrus bergylta",
            "Solea solea",
            "Trachurus trachurus",
        ],
        description=(
            "UK coastal kelp eDNA, carrying both vertebrate markers. Not a mock "
            "community: the expected list is the fish recovered consistently by "
            "both markers and confirmed as English Channel species, so it is a "
            "regression check rather than ground truth."
        ),
        citation="Clark et al. 2024.",
        notes=(
            "The same samples carry MarVer3 16S, so running this dataset with "
            "the MarVer3_16S primer set checks that marker too."
        ),
    ),
}


def get(key: str) -> Optional[ValidationDataset]:
    return DATASETS.get(key)


def describe_all() -> str:
    """A readable summary of every dataset, for the command line."""
    lines = []
    for dataset in DATASETS.values():
        lines.append(f"{dataset.key}  ({dataset.marker}, {dataset.bioproject})")
        lines.append(f"  {dataset.description}")
        lines.append(f"  Runs:     {', '.join(dataset.runs)}")
        lines.append(f"  Expected: {', '.join(dataset.expected)}")
        if dataset.notes:
            lines.append(f"  Note:     {dataset.notes}")
        lines.append(f"  Source:   {dataset.citation}")
        lines.append("")
    return "\n".join(lines)

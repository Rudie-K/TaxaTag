# src/analysis/sites.py
"""
Samples pooled into sites through a sample sheet: step 2 of the Analysis
tab's tier 1 (`docs/planned.md` item 4), decided in `docs/decisions/0033`.

The sheet is the user's. TaxaTag reads a CSV with a column naming each
sample - `Sample` unless told otherwise - and a `Site` column, and takes
both literally: it does not guess that `12.3` is site 12, replicate 3.
An optional `Control` column marks negative controls, which are never
pooled. Every sample the sheet and the run do not agree on is listed,
both ways.

At a site, following section 22 of `docs/science/what-the-literature-says.md`:

- **A taxon is present if any replicate detected it**, and the count
  behind that - detected in *k* of *K* - is reported beside it. There is
  no threshold on *k* (Ficetola et al. 2016).
- **The counting rule of decision 0032 runs after pooling:** a genus call
  is folded when any replicate of the site named a species of it.
- **Shannon and Simpson diversity come from the replicates' mean relative
  abundances**, not from summed reads, which would weight each replicate
  by its sequencing depth. The summed reads are written too, as the raw
  record.
- **Every site says how many replicates it pooled.** Pooled richness grows
  with replicates, so sites with different numbers are not comparable as
  they stand; equal-effort comparison is step 3.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Set

from src.analysis import diversity
from src.pipeline import layout

TRUE_WORDS = {"yes", "y", "true", "1", "x", "control"}

SITE_COLUMNS = [
    "Locus", "Marker", "Site", "Replicates", "Richness", "Species_Level_Taxa",
    "Shannon_Entropy", "Shannon_Diversity", "Simpson_Diversity",
    "Taxa_In_Every_Replicate", "Share_In_Every_Replicate", "Mean_Replicate_Jaccard",
    "Reads", "Counted_Reads", "Unidentified_Reads", "Contaminant_Reads",
    "Folded_Reads", "Coarser_Reads",
]
OCCURRENCE_COLUMNS = [
    "Locus", "Marker", "Site", "Rank", "Taxon", "Detections", "Replicates",
    "Frequency_Of_Occurrence", "Reads",
]
SITE_BETA_COLUMNS = [
    "Locus", "Marker", "Site_A", "Site_B", "Replicates_A", "Replicates_B",
    "Shared", "Only_A", "Only_B", "Sorensen", "Jaccard", "Turnover", "Nestedness",
]


class SheetError(ValueError):
    """A sample sheet that cannot be used as it stands, and why."""


@dataclass
class SampleSheet:
    """Which site each sample belongs to, as the user wrote it."""

    path: Path
    sample_column: str
    site_of: Dict[str, str] = field(default_factory=dict)
    controls: Set[str] = field(default_factory=set)
    without_site: Set[str] = field(default_factory=set)
    rows: List[Dict[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, sample_column: str = "Sample", site_column: str = "Site") -> "SampleSheet":
        with open(path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        columns = list(rows[0]) if rows else []
        for needed in (sample_column, site_column):
            if needed not in columns:
                raise SheetError(f"{path.name} has no '{needed}' column; its columns are: {', '.join(columns)}")
        control_column = next((c for c in columns if c.strip().lower() == "control"), None)
        sheet = cls(path=Path(path), sample_column=sample_column, rows=rows)
        for row in rows:
            sample = (row.get(sample_column) or "").strip()
            if not sample:
                continue
            if control_column and (row.get(control_column) or "").strip().lower() in TRUE_WORDS:
                sheet.controls.add(sample)
                continue
            site = (row.get(site_column) or "").strip()
            if site:
                sheet.site_of[sample] = site
            else:
                sheet.without_site.add(sample)
        return sheet

    def named(self) -> Set[str]:
        return set(self.site_of) | self.controls | self.without_site

    def column_that_matches(self, samples: Set[str]) -> Optional[str]:
        """The sheet column whose values match most of the run's samples."""
        best, best_count = None, 0
        for column in (self.rows[0] if self.rows else {}):
            count = len({(row.get(column) or "").strip() for row in self.rows} & samples)
            if count > best_count:
                best, best_count = column, count
        return best


def check(sheet: SampleSheet, run_samples: Set[str]) -> Dict[str, List[str]]:
    """Where the sheet and the run disagree. Raises when nothing matches."""
    if not sheet.named() & run_samples:
        hint = sheet.column_that_matches(run_samples)
        where = (f" The '{hint}' column does - give --sample-column {hint}." if hint else
                 " No column of the sheet holds this run's sample names.")
        raise SheetError(f"no value in the '{sheet.sample_column}' column of {sheet.path.name} "
                         f"matches a sample in this run.{where}")
    return {
        "in the run, not in the sheet": sorted(run_samples - sheet.named()),
        "in the sheet, not in the run": sorted(sheet.named() - run_samples),
        "in the sheet with no site": sorted(sheet.without_site & run_samples),
        "controls, not pooled": sorted(sheet.controls & run_samples),
    }


def _mean_shares(per_replicate: List[Dict[diversity.Taxon, int]], counted: Set[diversity.Taxon]) -> Dict:
    """
    Each counted taxon's mean share of reads across the replicates, every
    replicate weighted equally (Jost 2006; Alberdi and Gilbert 2019). A
    replicate with no reads in any counted taxon has no shares to give and
    is left out of the mean rather than counted as all zeros.
    """
    totals: Dict[diversity.Taxon, float] = defaultdict(float)
    used = 0
    for reads in per_replicate:
        depth = sum(reads.get(t, 0) for t in counted)
        if not depth:
            continue
        used += 1
        for taxon in counted:
            totals[taxon] += reads.get(taxon, 0) / depth
    return {taxon: share / used for taxon, share in totals.items()} if used else {}


def _jaccard_similarity(a: set, b: set) -> Optional[float]:
    union = a | b
    return len(a & b) / len(union) if union else None


def pool(run_dir: Path, sheet: SampleSheet, basis: str = diversity.MIXED,
         keep_contaminants: bool = False) -> Dict[str, object]:
    """Every step 2 table, as rows, without writing anything."""
    table = diversity.read_table(run_dir)
    sequenced = diversity.sequenced_samples(run_dir, table)
    rows_of: Dict[tuple, List[Dict[str, str]]] = defaultdict(list)
    markers: Dict[str, str] = {}
    for row in table:
        rows_of[(row["Locus"], row["Sample"])].append(row)
        markers[row["Locus"]] = row.get("Marker", "")
    disagreements = check(sheet, set().union(*sequenced.values()) if sequenced else set())

    sites, occurrence, beta, presence, reads_matrix = [], [], [], [], []
    for locus in sorted(sequenced):
        marker = markers.get(locus, "")
        replicates_of: Dict[str, List[str]] = defaultdict(list)
        for sample in sorted(sequenced[locus]):
            if sample in sheet.site_of:
                replicates_of[sheet.site_of[sample]].append(sample)

        taxa_of, summed_of = {}, {}
        for site in sorted(replicates_of, key=_natural):
            samples = replicates_of[site]
            calls_by_replicate, lineage = [], {}
            aside = {"Unidentified_Reads": 0, "Contaminant_Reads": 0, "Folded_Reads": 0, "Coarser_Reads": 0}
            for sample in samples:
                calls, their_lineage, sample_aside, _ = diversity._calls(
                    rows_of.get((locus, sample), []), basis, keep_contaminants)
                calls_by_replicate.append(calls)
                lineage.update(their_lineage)
                for reason, count in sample_aside.items():
                    aside[reason] += count

            pooled: Dict[diversity.Taxon, int] = defaultdict(int)
            for calls in calls_by_replicate:
                for taxon, count in calls.items():
                    pooled[taxon] += count
            folded = diversity.folded_away(pooled, lineage) if basis == diversity.MIXED else set()
            aside["Folded_Reads"] += sum(pooled[t] for t in folded)
            counted = {t: r for t, r in pooled.items() if t not in folded}
            taxa_of[site], summed_of[site] = set(counted), counted

            detected_in = [set(calls) & set(counted) for calls in calls_by_replicate]
            detections = {t: sum(t in found for found in detected_in) for t in counted}
            every = sum(1 for t in counted if detections[t] == len(samples))
            pairs = [s for s in (_jaccard_similarity(a, b) for a, b in combinations(detected_in, 2)) if s is not None]
            shares = _mean_shares(calls_by_replicate, set(counted))
            sites.append({
                "Locus": locus, "Marker": marker, "Site": site, "Replicates": len(samples),
                "Richness": len(counted),
                "Species_Level_Taxa": sum(1 for rank, _ in counted if rank == "Species"),
                **diversity.hill_numbers(shares.values()),
                "Taxa_In_Every_Replicate": every,
                "Share_In_Every_Replicate": every / len(counted) if counted else None,
                "Mean_Replicate_Jaccard": sum(pairs) / len(pairs) if pairs else None,
                "Reads": sum(int(float(r.get("Reads") or 0)) for s in samples for r in rows_of.get((locus, s), [])),
                "Counted_Reads": sum(counted.values()), **aside,
            })
            for rank, name in sorted(counted, key=lambda t: (-diversity._rank_index(t[0]), t[1])):
                occurrence.append({
                    "Locus": locus, "Marker": marker, "Site": site, "Rank": rank, "Taxon": name,
                    "Detections": detections[(rank, name)], "Replicates": len(samples),
                    "Frequency_Of_Occurrence": detections[(rank, name)] / len(samples),
                    "Reads": counted[(rank, name)],
                })

        for first, second in combinations(sorted(replicates_of, key=_natural), 2):
            beta.append({"Locus": locus, "Marker": marker, "Site_A": first, "Site_B": second,
                         "Replicates_A": len(replicates_of[first]), "Replicates_B": len(replicates_of[second]),
                         **diversity.dissimilarity(taxa_of[first], taxa_of[second])})
        every_taxon = sorted(set().union(*taxa_of.values()) if taxa_of else set(),
                             key=lambda t: (-diversity._rank_index(t[0]), t[1]))
        for rank, name in every_taxon:
            key = {"Locus": locus, "Marker": marker, "Rank": rank, "Taxon": name}
            counts = {site: summed_of[site].get((rank, name), 0) for site in replicates_of}
            reads_matrix.append({**key, **counts})
            presence.append({**key, **{site: int(c > 0) for site, c in counts.items()}})

    return {"sites": sites, "occurrence": occurrence, "beta": beta, "presence": presence,
            "reads": reads_matrix, "disagreements": disagreements}


NOTES = """
Sites (site*.csv), pooled through {sheet} ({column} column matched to the run's samples)
Presence: a taxon is present at a site if any replicate detected it. Detections
  and Replicates say how many (Frequency_Of_Occurrence = Detections / Replicates).
  No threshold on detections is applied (Ficetola et al. 2016).
Counting rule: as above, applied after pooling - a call is folded when any
  replicate of the site named something finer inside it.
Reads: site-reads.csv sums each taxon's reads over the replicates. Shannon and
  Simpson diversity at a site are computed from the replicates' mean relative
  abundances instead, each replicate weighted equally, so that sequencing depth
  does not decide a replicate's weight.
Replicates: every site row says how many samples it pooled. Pooled richness
  grows with replicates; sites with different numbers are not comparable as they
  stand.{unequal}
Consistency: Share_In_Every_Replicate is the share of a site's taxa found in
  every replicate; Mean_Replicate_Jaccard the mean Jaccard similarity between
  its replicates' taxa. Blank for a site with one replicate.
Not recovered by pooling: each sample was denoised on its own, discarding
  sequences seen fewer than the run's minimum ZOTU size, so a taxon below it in
  every replicate is absent from all of them before pooling.
Negative controls: never pooled. Their reads are not subtracted from anything.
{disagreements}"""


def write_sites(run_dir: Path, sheet: SampleSheet, basis: str = diversity.MIXED,
                keep_contaminants: bool = False, out_dir: Optional[Path] = None) -> Dict[str, object]:
    """Write step 2's tables beside step 1's, and add to the notes."""
    tables = pool(run_dir, sheet, basis, keep_contaminants)
    folder = Path(out_dir) if out_dir else layout.analysis_dir(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    suffix = "" if basis == diversity.MIXED else f"-{basis}"
    names = sorted({row["Site"] for row in tables["sites"]}, key=_natural)
    written = {
        "sites": diversity._write(folder / f"sites{suffix}.csv", SITE_COLUMNS, tables["sites"]),
        "occurrence": diversity._write(folder / f"site-occurrence{suffix}.csv", OCCURRENCE_COLUMNS, tables["occurrence"]),
        "beta": diversity._write(folder / f"site-beta{suffix}.csv", SITE_BETA_COLUMNS, tables["beta"]),
        "presence": diversity._write(folder / f"site-presence{suffix}.csv", diversity.MATRIX_KEY + names, tables["presence"]),
        "reads": diversity._write(folder / f"site-reads{suffix}.csv", diversity.MATRIX_KEY + names, tables["reads"]),
    }
    counts = {(row["Locus"], row["Replicates"]) for row in tables["sites"]}
    unequal = sorted({locus for locus, _ in counts if len({n for l, n in counts if l == locus}) > 1})
    lines = []
    for heading, samples in tables["disagreements"].items():
        if samples:
            shown = ", ".join(samples[:12]) + (f" and {len(samples) - 12} more" if len(samples) > 12 else "")
            lines.append(f"Samples {heading} ({len(samples)}): {shown}")
    notes = folder / f"diversity{suffix}-notes.txt"
    with open(notes, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(NOTES.format(
            sheet=sheet.path.name, column=sheet.sample_column,
            unequal=(f"\n  Replicate numbers differ between sites on: {', '.join(unequal)}." if unequal else ""),
            disagreements="\n".join(lines) + ("\n" if lines else ""),
        ))
    return {"written": written, "tables": tables, "unequal": unequal}


def _natural(name: str):
    """Site 2 before site 10, and names after numbers."""
    return (0, int(name), "") if name.isdigit() else (1, 0, name)

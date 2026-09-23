# src/analysis/diversity.py
"""
The first, standard description of a finished run: what each sample holds,
how many taxa, how evenly its reads fall among them, and how samples differ.

Step 1 of the Analysis tab's tier 1 (`docs/planned.md` item 4), decided in
`docs/decisions/0032`, where the literature behind every number is. In
short:

- **Per marker, always.** Two markers are two samplings of a community.
- **Each sample is a unit.** Nothing is pooled without a sample sheet.
- **A call counts once, at the finest rank it was named.** A call above
  species counts only when nothing finer inside that taxon was named in
  the same sample: a *Trachurus* call beside *Trachurus trachurus* is
  folded, not counted twice. No sequence difference can say whether it
  was a second species, so richness is a minimum.
- **Unidentified is not a taxon.** It is counted, in its own column.
- **Contaminants are set aside by default** (decision 0012's genera), and
  counted, never silently dropped.
- **Reads are reads.** Shannon and Simpson diversity are Hill numbers of
  order 1 and 2 computed from reads, named as Chao et al. (2014) name
  them; a read share is not an abundance (Lamb et al. 2019).
- **Beta diversity is presence arithmetic**: Sørensen and Jaccard
  dissimilarity, Sørensen split into turnover and nestedness (Baselga
  2010), with the counts behind each so any row can be checked by hand.

Nothing here estimates what was not seen. TaxaTag's reads cannot support
it - every ZOTU carries at least `min_zotu_size` reads - and completeness
across samples is step 3.
"""

from __future__ import annotations

import csv
import math
import statistics
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from src.analysis import readiness
from src.pipeline import layout
from src.pipeline.stage4_blast import RANK_ORDER, UNIDENTIFIED_RANK
from src.reference import contaminants

#: The default: every call at the finest rank it reached, under the
#: folding rule. The alternative is one fixed rank for every call.
MIXED = "mixed"
FIXED_RANKS = ("species", "genus", "family")

SUMMARY_COLUMNS = [
    "Locus", "Marker", "Sample", "Reads", "ZOTUs",
    "Richness", "Species_Level_Taxa",
    "Shannon_Entropy", "Shannon_Diversity", "Simpson_Diversity",
    "Counted_Reads", "Unidentified_ZOTUs", "Unidentified_Reads",
    "Contaminant_Reads", "Folded_Reads", "Coarser_Reads", "Caution",
]
BETA_COLUMNS = [
    "Locus", "Marker", "Sample_A", "Sample_B", "Shared", "Only_A", "Only_B",
    "Sorensen", "Jaccard", "Turnover", "Nestedness",
]
MATRIX_KEY = ["Locus", "Marker", "Rank", "Taxon"]

#: A taxon: the rank it is counted at, and its name there.
Taxon = Tuple[str, str]


def _rank_index(rank: str) -> int:
    """Position in RANK_ORDER, finest last; -1 for anything unranked."""
    return RANK_ORDER.index(rank) if rank in RANK_ORDER else -1


def _name_at(row: Dict[str, str], rank: str) -> str:
    """
    The row's taxon at `rank`, from its lineage columns. A binomial's first
    word stands in for a missing Genus column, which a raw-database run
    leaves empty until the taxonomy stage fills it.
    """
    value = (row.get(rank) or "").strip()
    if value:
        return value
    if row["Rank"] == rank:
        return row["Scientific_Name"].strip()
    if rank == "Genus" and _rank_index(row["Rank"]) > _rank_index("Genus"):
        return row["Scientific_Name"].strip().split(" ")[0]
    return ""


def read_table(run_dir: Path) -> List[Dict[str, str]]:
    """
    The species table - from the taxonomy stage's copy when there is one.
    That copy holds the same rows with the lineage filled in, which a run
    searched against a raw NCBI database otherwise lacks; without it a
    family-level call cannot be checked for a species inside it (0034).
    """
    folder = layout.identification_dir(run_dir)
    taxonomy = folder / layout.FINAL_TAXONOMY_TABLE
    if taxonomy.exists():
        with open(taxonomy, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if rows and {"Sample", "Locus", "Rank", "Reads", "Scientific_Name"} <= set(rows[0]):
            return rows
    with open(folder / layout.FINAL_SPECIES_TABLE, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def depths(run_dir: Path) -> Dict[Tuple[str, str], int]:
    """Each sample's reads per locus, from the dereplication report; empty without it."""
    found: Dict[Tuple[str, str], int] = {}
    report = layout.report_csv(run_dir, "dereplication")
    if report.exists():
        with open(report, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    found[(row["Locus"], row["Sample"])] = int(row.get("Total_Reads") or 0)
                except (KeyError, ValueError):
                    continue
    return found


def sequenced_samples(run_dir: Path, table: List[Dict[str, str]]) -> Dict[str, set]:
    """
    Every sample that was sequenced, per locus. The species table holds only
    samples with at least one row, so a sample whose reads were all
    discarded would vanish from it - and a sample that vanishes is a sample
    whose richness of zero is never reported, or a replicate that is never
    counted. The dereplication report lists every sample with its reads.
    """
    found: Dict[str, set] = defaultdict(set)
    for row in table:
        found[row["Locus"]].add(row["Sample"])
    for (locus, sample), reads in depths(run_dir).items():
        if reads > 0:
            found[locus].add(sample)
    return found


def _calls(rows: List[Dict[str, str]], basis: str, keep_contaminants: bool):
    """
    One sample's rows (one locus) as calls, before folding: ({taxon: reads},
    {taxon: its name at every rank}, {reason: reads set aside}, unidentified
    ZOTUs). Kept apart from `folded_away` so that replicates can be pooled first
    and folded together.
    """
    aside = {"Unidentified_Reads": 0, "Contaminant_Reads": 0,
             "Folded_Reads": 0, "Coarser_Reads": 0}
    unidentified = 0
    calls: Dict[Taxon, int] = defaultdict(int)
    lineage: Dict[Taxon, Dict[str, str]] = {}
    for row in rows:
        reads = int(float(row.get("Reads") or 0))
        rank = (row.get("Rank") or "").strip()
        if rank == UNIDENTIFIED_RANK or rank not in RANK_ORDER:
            unidentified += 1
            aside["Unidentified_Reads"] += reads
            continue
        if not keep_contaminants and contaminants.is_likely_contaminant(
                row.get("Scientific_Name", ""), _name_at(row, "Genus")):
            aside["Contaminant_Reads"] += reads
            continue
        if basis == MIXED:
            taxon = (rank, row["Scientific_Name"].strip())
        else:
            fixed = basis.capitalize()
            if _rank_index(rank) < _rank_index(fixed):
                aside["Coarser_Reads"] += reads
                continue
            taxon = (fixed, _name_at(row, fixed))
        calls[taxon] += reads
        lineage.setdefault(taxon, {r: _name_at(row, r) for r in RANK_ORDER})
    return dict(calls), lineage, aside, unidentified


def folded_away(calls: Iterable[Taxon], lineage: Dict[Taxon, Dict[str, str]]) -> set:
    """
    The calls the folding rule does not count: those with something finer
    named inside them. `lineage` holds each call's name at every rank, so
    "inside" is "has this call's name at this call's rank".
    """
    calls = list(calls)
    return {
        (rank, name) for rank, name in calls
        if any(_rank_index(other_rank) > _rank_index(rank)
               and lineage[(other_rank, other_name)].get(rank) == name
               for other_rank, other_name in calls)
    }


def _counted(rows: List[Dict[str, str]], basis: str, keep_contaminants: bool):
    """
    One sample's rows (one locus) sorted into what is counted and what is
    set aside. Returns ({taxon: reads}, {reason: reads}, unidentified ZOTUs).
    """
    calls, lineage, aside, unidentified = _calls(rows, basis, keep_contaminants)
    if basis != MIXED:
        return calls, aside, unidentified
    folded = folded_away(calls, lineage)
    aside["Folded_Reads"] += sum(calls[t] for t in folded)
    return {t: r for t, r in calls.items() if t not in folded}, aside, unidentified


def hill_numbers(reads: Iterable[int]) -> Dict[str, Optional[float]]:
    """
    Shannon entropy (natural log) and the Hill numbers of order 1 and 2
    from read counts: Shannon diversity exp(H) and Simpson diversity
    1 / sum(p^2) (Jost 2006; Chao et al. 2014). None for an empty sample.
    """
    counts = [r for r in reads if r > 0]
    total = sum(counts)
    if not total:
        return {"Shannon_Entropy": None, "Shannon_Diversity": None, "Simpson_Diversity": None}
    shares = [c / total for c in counts]
    entropy = -sum(p * math.log(p) for p in shares)
    return {
        "Shannon_Entropy": entropy,
        "Shannon_Diversity": math.exp(entropy),
        "Simpson_Diversity": 1.0 / sum(p * p for p in shares),
    }


def dissimilarity(a: set, b: set) -> Dict[str, object]:
    """
    Sørensen and Jaccard dissimilarity between two sets of taxa, and
    Sørensen split into turnover (Simpson dissimilarity) and nestedness
    (Baselga 2010). An index whose denominator is zero is left blank:
    two empty samples have no composition to compare.
    """
    shared, only_a, only_b = len(a & b), len(a - b), len(b - a)
    low = min(only_a, only_b)
    sorensen = (only_a + only_b) / (2 * shared + only_a + only_b) if (shared or only_a or only_b) else None
    jaccard = (only_a + only_b) / (shared + only_a + only_b) if (shared or only_a or only_b) else None
    turnover = low / (shared + low) if (shared + low) else None
    nestedness = sorensen - turnover if sorensen is not None and turnover is not None else None
    return {"Shared": shared, "Only_A": only_a, "Only_B": only_b, "Sorensen": sorensen,
            "Jaccard": jaccard, "Turnover": turnover, "Nestedness": nestedness}


def _cautions(locus: str, sample: str, counted: Dict[Taxon, int], depth: Dict[Tuple[str, str], int],
              median: Dict[str, float]) -> List[readiness.Finding]:
    """What decision 0034's conditions say about one sample."""
    cautions = []
    reads = sum(counted.values())
    if not counted:
        cautions.append(readiness.found("nothing-counted", locus, sample))
    elif reads < readiness.FEW_READS:
        cautions.append(readiness.found("few-reads", locus, sample, reads=reads))
    sequenced = depth.get((locus, sample))
    if sequenced is not None and median.get(locus) and sequenced < readiness.SHALLOW_FRACTION * median[locus]:
        cautions.append(readiness.found("low-depth", locus, sample, reads=f"{sequenced:,}",
                                        median=f"{int(median[locus]):,}"))
    return cautions


def describe(run_dir: Path, basis: str = MIXED, keep_contaminants: bool = False) -> Dict[str, list]:
    """Every table step 1 writes, as rows, and every finding, without writing anything."""
    if basis != MIXED and basis not in FIXED_RANKS:
        raise ValueError(f"rank must be {MIXED} or one of {', '.join(FIXED_RANKS)}")
    by_sample: Dict[Tuple[str, str], List[Dict[str, str]]] = defaultdict(list)
    markers: Dict[str, str] = {}
    table = read_table(run_dir)
    for row in table:
        by_sample[(row["Locus"], row["Sample"])].append(row)
        markers[row["Locus"]] = row.get("Marker", "")
    for locus, samples in sequenced_samples(run_dir, table).items():
        markers.setdefault(locus, "")
        for sample in samples:
            by_sample.setdefault((locus, sample), [])

    findings = readiness.run_findings(run_dir, table)
    depth = depths(run_dir)
    median = {}
    for locus in markers:
        values = [depth[(locus, s)] for (l, s) in by_sample if l == locus and (locus, s) in depth]
        if values:
            median[locus] = statistics.median(values)

    summary, taxa_of, reads_of = [], {}, {}
    for (locus, sample), rows in sorted(by_sample.items()):
        counted, aside, unidentified = _counted(rows, basis, keep_contaminants)
        taxa_of[(locus, sample)] = set(counted)
        reads_of[(locus, sample)] = counted
        cautions = _cautions(locus, sample, counted, depth, median)
        findings += cautions
        summary.append({
            "Locus": locus, "Marker": markers[locus], "Sample": sample,
            "Reads": sum(int(float(r.get("Reads") or 0)) for r in rows),
            "ZOTUs": len({r["ZOTU"] for r in rows}),
            "Richness": len(counted),
            "Species_Level_Taxa": sum(1 for rank, _ in counted if rank == "Species"),
            **hill_numbers(counted.values()),
            "Counted_Reads": sum(counted.values()),
            "Unidentified_ZOTUs": unidentified, **aside,
            "Caution": "; ".join(f.code for f in cautions),
        })

    beta, presence, reads = [], [], []
    for locus in sorted(markers):
        samples = sorted(s for (l, s) in by_sample if l == locus)
        if len(samples) < 2:
            findings.append(readiness.found("one-unit", locus, units="samples", count=len(samples)))
        for first, second in combinations(samples, 2):
            beta.append({"Locus": locus, "Marker": markers[locus], "Sample_A": first, "Sample_B": second,
                         **dissimilarity(taxa_of[(locus, first)], taxa_of[(locus, second)])})
        every = sorted(set().union(*(taxa_of[(locus, s)] for s in samples)),
                       key=lambda t: (-_rank_index(t[0]), t[1]))
        for rank, name in every:
            key = {"Locus": locus, "Marker": markers[locus], "Rank": rank, "Taxon": name}
            counts = {s: reads_of[(locus, s)].get((rank, name), 0) for s in samples}
            reads.append({**key, **counts})
            presence.append({**key, **{s: int(c > 0) for s, c in counts.items()}})
    return {"summary": summary, "beta": beta, "presence": presence, "reads": reads, "findings": findings}


def _format(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _write(path: Path, columns: List[str], rows: List[Dict[str, object]]) -> Path:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        # restval "": a sample that was not sequenced for this marker is
        # blank, which R reads as NA, not 0, which would claim an absence.
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n", restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _format(v) for k, v in row.items()})
    return path


NOTES = """TaxaTag diversity, step 1 - how these numbers were made
Counting basis: {basis}
{basis_text}
Per marker: every number is for one locus; markers are never pooled.
Units: each sample is its own unit; nothing was pooled into sites.
Unidentified: not a taxon; its ZOTUs and reads are in their own columns.
Contaminants: {contaminant_text}
Reads: Shannon_Entropy uses the natural log. Shannon_Diversity = exp(H) and
  Simpson_Diversity = 1 / sum(p^2) are Hill numbers of order 1 and 2 (Chao et
  al. 2014), computed from the reads of counted taxa only. A share of reads
  is not a share of individuals or biomass (Lamb et al. 2019).
Beta diversity (beta*.csv): presence only. Sorensen = (b+c)/(2a+b+c),
  Jaccard = (b+c)/(a+b+c), Turnover = min(b,c)/(a+min(b,c)) and
  Nestedness = Sorensen - Turnover (Baselga 2010); a = Shared, b = Only_A,
  c = Only_B. Blank where the denominator is zero.
Presence (presence*.csv): 1 = detected, 0 = not detected - which is not the
  same as absent: eDNA can miss a species that is there (Ficetola et al. 2016).
Not estimated: unseen species. Every taxon carries at least the run's
  minimum ZOTU size in reads, so read-based estimators (Chao1, coverage) would
  report every sample complete.
Decision records: TaxaTag docs/decisions/0032 and 0034.
{cautions}"""

MIXED_TEXT = (
    "A call counts once, at the finest rank it was named. A call above species\n"
    "counts only when nothing finer inside that taxon was named in the same\n"
    "sample; otherwise its reads are in Folded_Reads. Richness is therefore a\n"
    "minimum: a second species named only to genus is folded into the first."
)
FIXED_TEXT = (
    "Every call named to {rank} or finer is counted as its {rank}; calls that\n"
    "stopped above {rank} are not counted, and their reads are in Coarser_Reads."
)


def write_diversity(run_dir: Path, basis: str = MIXED, keep_contaminants: bool = False,
                    out_dir: Optional[Path] = None) -> Dict[str, object]:
    """
    Write step 1's tables into the run's `06_analysis/` (or `out_dir`).
    Returns the paths written, the tables, and the findings.
    """
    tables = describe(run_dir, basis, keep_contaminants)
    folder = Path(out_dir) if out_dir else layout.analysis_dir(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    suffix = "" if basis == MIXED else f"-{basis}"
    samples = sorted({row["Sample"] for row in tables["summary"]})
    written = {
        "summary": _write(folder / f"diversity{suffix}.csv", SUMMARY_COLUMNS, tables["summary"]),
        "beta": _write(folder / f"beta{suffix}.csv", BETA_COLUMNS, tables["beta"]),
        "presence": _write(folder / f"presence{suffix}.csv", MATRIX_KEY + samples, tables["presence"]),
        "reads": _write(folder / f"reads{suffix}.csv", MATRIX_KEY + samples, tables["reads"]),
    }
    notes = folder / f"diversity{suffix}-notes.txt"
    notes.write_text(NOTES.format(
        basis=basis,
        basis_text=MIXED_TEXT if basis == MIXED else FIXED_TEXT.format(rank=basis),
        contaminant_text=("kept, as asked (--keep-contaminants)." if keep_contaminants else
                          "set aside, and their reads counted in Contaminant_Reads\n  (" +
                          ", ".join(sorted(contaminants.CONTAMINANT_GENERA)) + ")."),
        cautions=readiness.notes_section(tables["findings"]),
    ), encoding="utf-8", newline="\n")
    written["notes"] = notes
    return {"written": written, "tables": tables, "findings": tables["findings"]}

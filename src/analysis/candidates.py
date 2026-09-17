# src/analysis/candidates.py
"""
Every name a sequence could have had, not only the one it was given.

The species table reports one call per ZOTU. Behind it, stage 4 kept every
reference the sequence matched (`05_results/blast_hits_<marker>.tsv`), and
this writes them out, named, ranked and flagged: the ten best per ZOTU
with identity, bitscore, whether each lies within the tie margin that
decided the call, and how well the library was placed to name it.

Two things it makes visible that the species table cannot:

* **A confident call is not the same as a safe one.** A sequence that
  matched one reference at 100% with the runner-up far behind looks
  certain, and is exactly the case where the species that was really
  there may have no reference at all - the nearest relative wins cleanly.
  The library's coverage of the winning species and its genus
  (`Species_References`, `Genus_References`, `Genus_Species`) is the
  evidence for that: one reference and no congeners is Bourret et al.'s
  *unreliable due to gaps*.
* **Ambiguity has kinds.** When the tied references name more than one
  species, whether they share a genus or span families is a different
  problem with a different cause. The `Flag` column says which, in
  Macher et al.'s (2023) terms, so the two are counted apart.

Reads from a finished run; writes `06_analysis/candidates.csv`; never
changes the run. `docs/science/what-the-literature-says.md`, sections 3,
8 and 10; `docs/planned.md` item 4, tier 2.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from src.pipeline import layout
from src.pipeline.stage4_blast import BLAST_FIELDS, TIE_MARGIN

#: How many candidates to keep per ZOTU. Ten is what people ask for; the
#: hits file holds the rest.
TOP_N = 10

#: Macher et al. (2023): what remained after the best-scoring references
#: were compared. Written as the paper writes them so the counts are
#: comparable with a published scheme rather than a private one.
FLAG_NONE = ""                                  # one species, or none at species rank
FLAG_F1 = "F1 dominant taxon"                   # several, one outnumbering the rest
FLAG_F2 = "F2 two species, one genus"
FLAG_F3 = "F3 multiple species, one genus"
FLAG_F4 = "F4 multiple genera"

COLUMNS = [
    "Sample", "Locus", "Marker", "ZOTU", "Reads", "Call", "Call_Rank", "Flag",
    "Candidate_Rank", "Candidate", "Candidate_Genus", "Candidate_Family",
    "Identity_Percent", "Query_Coverage_Percent", "Bitscore", "Within_Tie_Margin",
    "Species_References", "Genus_References", "Genus_Species",
    "Reference", "Library_ID",
]


# ---------------------------------------------------------------- reading


def read_hits(path: Path) -> Dict[str, List[Dict]]:
    """The hits file, grouped by query and sorted best first."""
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle, delimiter="\t"):
            if len(row) < 7:
                continue
            record = dict(zip(BLAST_FIELDS, row))
            grouped[record["qseqid"].replace("lcl|", "")].append({
                "subject": record["sseqid"],
                "identity": float(record["pident"]),
                "coverage": float(record["qcovhsp"]),
                "bitscore": float(record["bitscore"]),
                # A raw NCBI database reports these; the library's own volumes write N/A.
                "taxid": (record.get("staxids") or "").split(";")[0].strip(),
            })
    for hits in grouped.values():
        hits.sort(key=lambda h: (-h["bitscore"], -h["identity"], h["subject"]))
    return grouped


def _lineages_from_taxids(hits: Dict[str, List[Dict]], library) -> Dict[str, object]:
    """A lineage record per hit from its taxid, shaped like the catalogue's answer."""
    from src.reference.library import ReferenceRecord

    taxids = {h.get("taxid", "") for group in hits.values() for h in group if h.get("taxid") and h["taxid"] not in ("N/A", "0")}
    by_taxid = library.lineages_by_taxid(taxids) if taxids else {}
    found = {}
    for group in hits.values():
        for h in group:
            lineage = by_taxid.get(h.get("taxid", ""))
            if lineage and any(lineage.values()):
                found[h["subject"]] = ReferenceRecord(accession=h["subject"], lineage=lineage,
                                                      source_accession=h["subject"].split("|")[-1] if "|" in h["subject"] else h["subject"])
    return found


def read_queries(path: Path) -> Dict[str, str]:
    """Sequence -> query id, from the FASTA stage 4 searched with."""
    by_sequence: Dict[str, str] = {}
    query_id, chunks = None, []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith(">"):
                if query_id is not None:
                    by_sequence["".join(chunks)] = query_id
                query_id, chunks = line[1:].split()[0], []
            elif line:
                chunks.append(line)
    if query_id is not None:
        by_sequence["".join(chunks)] = query_id
    return by_sequence


def read_table(path: Path) -> List[Dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


# ---------------------------------------------------------------- deciding


def flag(tied: Iterable[Dict], threshold: float = 0.9) -> str:
    """
    Which of Macher et al.'s cases the tied references present.

    `tied` carries `species` and `genus` per reference (empty when the
    reference is not named at that rank). References unnamed at species do
    not count towards the tally, for the reason decision 0003 gives: a
    reference identified only to class should not veto a species every
    other reference agrees on.
    """
    named = [t for t in tied if t.get("species")]
    species = [t["species"] for t in named]
    distinct = sorted(set(species))
    if len(distinct) <= 1:
        return FLAG_NONE
    if max(species.count(name) for name in distinct) / len(species) >= threshold:
        return FLAG_F1
    genera = {t.get("genus") or t["species"].split(" ")[0] for t in named}
    if len(genera) > 1:
        return FLAG_F4
    return FLAG_F2 if len(distinct) == 2 else FLAG_F3


def within_margin(hits: List[Dict], margin: float = TIE_MARGIN) -> List[bool]:
    if not hits:
        return []
    floor = hits[0]["bitscore"] * (1.0 - margin)
    return [h["bitscore"] >= floor for h in hits]


# ---------------------------------------------------------------- writing


def candidate_rows(run_dir: Path, library, top_n: int = TOP_N,
                   consensus_threshold: float = 0.9) -> List[Dict[str, str]]:
    """
    One row per (sample, ZOTU, candidate), for every marker the run searched.

    `library` needs `lookup(accessions)` and `coverage(marker, species,
    genus)`, which `ReferenceLibrary` has; anything with those two works,
    which is what the tests use.
    """
    results = layout.identification_dir(run_dir)
    table = read_table(results / "species_composition.csv")
    rows: List[Dict[str, str]] = []
    coverage_cache: Dict[Tuple[str, str], Dict[str, int]] = {}

    def coverage(marker: str, species: str, genus: str) -> Dict[str, int]:
        key = (marker, species)
        if key not in coverage_cache:
            coverage_cache[key] = library.coverage(marker, species, genus)
        return coverage_cache[key]

    for marker in sorted({row["Marker"] for row in table if row.get("Marker")}):
        hits_path = results / f"blast_hits_{marker}.tsv"
        query_path = results / f"query_{marker}.fasta"
        if not hits_path.exists() or not query_path.exists():
            continue                    # a remote (NCBI) search leaves neither
        hits = read_hits(hits_path)
        by_sequence = read_queries(query_path)
        subjects = {h["subject"] for group in hits.values() for h in group}
        lineages = library.lookup(subjects)
        if not lineages and hasattr(library, "lineages_by_taxid"):
            # The run searched a raw NCBI database (decision 0029): the hits are
            # NCBI accessions the catalogue does not hold, but each carries a
            # taxid, and the library's taxonomy names it from that.
            lineages = _lineages_from_taxids(hits, library)

        for row in (r for r in table if r.get("Marker") == marker):
            query_id = by_sequence.get(row.get("Sequence", ""))
            group = hits.get(query_id or "", [])
            if not group:
                continue
            margins = within_margin(group)
            tied = []
            for hit, tie in zip(group, margins):
                if not tie:
                    break
                record = lineages.get(hit["subject"])
                tied.append({"species": record.lineage.get("species", "") if record else "",
                             "genus": record.lineage.get("genus", "") if record else ""})
            zotu_flag = flag(tied, consensus_threshold)

            for position, (hit, tie) in enumerate(zip(group[:top_n], margins[:top_n]), 1):
                record = lineages.get(hit["subject"])
                lineage = record.lineage if record else {}
                species = lineage.get("species", "")
                genus = lineage.get("genus", "") or (species.split(" ")[0] if species else "")
                cover = coverage(marker, species, genus) if species else {}
                rows.append({
                    "Sample": row["Sample"], "Locus": row.get("Locus", ""), "Marker": marker,
                    "ZOTU": row["ZOTU"], "Reads": row.get("Reads", ""),
                    "Call": row.get("Scientific_Name", ""), "Call_Rank": row.get("Rank", ""),
                    "Flag": zotu_flag,
                    "Candidate_Rank": str(position),
                    "Candidate": record.best_name if record else hit["subject"],
                    "Candidate_Genus": genus, "Candidate_Family": lineage.get("family", ""),
                    "Identity_Percent": f"{hit['identity']:.2f}",
                    "Query_Coverage_Percent": f"{hit['coverage']:.0f}",
                    "Bitscore": f"{hit['bitscore']:.0f}",
                    "Within_Tie_Margin": "yes" if tie else "no",
                    "Species_References": str(cover.get("species_references", "")),
                    "Genus_References": str(cover.get("genus_references", "")),
                    "Genus_Species": str(cover.get("genus_species", "")),
                    "Reference": (record.source_accession if record else "") or "",
                    "Library_ID": hit["subject"],
                })
    return rows


def write_candidates(run_dir: Path, library, top_n: int = TOP_N) -> Path:
    """Write `06_analysis/candidates.csv` and return its path."""
    rows = candidate_rows(run_dir, library, top_n)
    folder = layout.analysis_dir(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "candidates.csv"
    # LF, as every table TaxaTag writes for other tools to read (0022's
    # CHECKSUMS lesson: a CRLF here would surprise a Unix reader silently).
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def summarise(rows: List[Dict[str, str]]) -> Dict[str, int]:
    """Counts a person would want printed: ZOTUs, and how many carry each flag."""
    seen: Dict[Tuple[str, str], str] = {}
    for row in rows:
        seen[(row["Sample"], row["ZOTU"])] = row["Flag"]
    summary = {"zotus": len(seen)}
    for name in (FLAG_F1, FLAG_F2, FLAG_F3, FLAG_F4):
        summary[name] = sum(1 for f in seen.values() if f == name)
    summary["unflagged"] = sum(1 for f in seen.values() if f == FLAG_NONE)
    return summary

# src/analysis/selfcheck.py
"""
A library tested with its own references, under the program's own rule.

    python -m src.analysis selfcheck --library <folder> --species-list <csv> --locus MiFish_12S --out <folder>

Before a survey's names are trusted, the library they came from can be
asked two questions that need no water sample (`planned.md` item 10;
literature digest section 14):

    leave one out    each reference of a listed species, cut to the
                     amplicon, is identified against the rest of the
                     library with only itself removed. The species the
                     marker cannot separate show here, before any survey
                     (Gold 2021's cross-validation of MiFish 12S; Edgar
                     2018's TAXXI).
    novel species    the same reference is identified with every
                     reference of its species removed, as if the species
                     had never been sequenced. The right answer is then
                     its genus, and naming a species is an
                     overclassification (Bokulich 2018's novel-taxon test).
                     This is the counterfactual for a call made over a
                     coverage gap, which no evidence in a survey can test.

The queries are the references' amplicons (`amplicons.py`), so they look
like reads. They are searched once against the whole marker volume, as a
run's reads are, with a larger hit cap; each test is the same hit list
with references removed, judged by `identify_sequence`, the rule stage 4
uses, with the thresholds of the configuration given. The expected
answer is the finest rank the rest of the library still holds among
references that carry the amplicon (Bokulich 2018), so a species with
one reference is expected at genus when that one is left out.

"Correct" means agreeing with the library's own labels. A mislabelled
record makes a right call look wrong, so the records the rule confidently
contradicts are listed as candidate mislabels, not as errors.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from src.analysis import amplicons
from src.analysis.adjudication import SpeciesList
from src.pipeline.stage4_blast import (
    REPORTED, UNIDENTIFIED_NAME, _build_blast_command, _parse_blast_results, identify_sequence,
)
from src.reference.library import LINEAGE_COLUMNS
from src.utils.process import run_tool

LEAVE_ONE_OUT, NOVEL_SPECIES = "leave one out", "novel species"
TESTS = (LEAVE_ONE_OUT, NOVEL_SPECIES)

#: The outcomes, in Edgar 2018's terms as Gold 2021 used them.
CORRECT = "correct"
OVERCLASSIFIED = "overclassified"          # named further down than the rest of the library allows
UNDERCLASSIFIED = "underclassified"        # right, but stopped short of the rank it could have reached
MISCLASSIFIED = "misclassified"            # a different name at a rank both reached
NOT_APPLICABLE = "not applicable"          # the library held nothing to name it by; left out of every rate
OUTCOMES = (CORRECT, OVERCLASSIFIED, UNDERCLASSIFIED, MISCLASSIFIED, NOT_APPLICABLE)

#: Ranks from the finest down, as their lineage columns.
RANKS_FINEST_FIRST = [column for column, _ in reversed(LINEAGE_COLUMNS)]
RANK_LABEL = dict(LINEAGE_COLUMNS)
LABEL_COLUMN = {label: column for column, label in LINEAGE_COLUMNS}


#: The most hits kept for one query. BLAST keeps the *first* N hits it
#: finds, in database order, not the best N (Shah 2019; digest section
#: 15), so a full list can be missing the very reference a hold-out
#: needed. The first run on the 12S volume, with the configuration's cap
#: plus the largest species (973), filled the list for 2,464 of 3,638
#: references. Searching only hits that could vote (below) leaves lists
#: short enough that this should never be reached; a row that reaches it
#: is flagged.
HIT_CAP = 20000


def _column(outcome: str) -> str:
    return outcome.capitalize().replace(" ", "_")


RECORD_COLUMNS = ["Marker", "Locus", "Species", "Record", "Source_Accession", "Cut_By", "Test", "Expected_Rank",
                  "Expected_Name", "Call", "Call_Rank", "Outcome", "Agreement", "Tied_Species", "Hits",
                  "Cap_Reached"]
SPECIES_COLUMNS = ["Marker", "Locus", "Species", "References_Cut", "Records_Not_Cut"] + [
    f"{prefix}_{_column(outcome)}" for prefix in ("Leave_One_Out", "Novel_Species") for outcome in OUTCOMES
] + ["Named_Instead", "Cap_Reached"]


@dataclasses.dataclass
class Expected:
    rank: str = ""          # a lineage column, or "" when nothing is left to name it by
    name: str = ""
    lineage: Dict[str, str] = dataclasses.field(default_factory=dict)   # the query's, at `rank` and above


@dataclasses.dataclass
class Judged:
    record: str
    test: str
    expected: Expected
    call: str
    call_rank: str
    call_lineage: Dict[str, str]
    outcome: str
    agreement: float
    tied_species: List[str]
    hits: int
    cap_reached: bool


# ---------------------------------------------------------------- what the rest of the library holds


class Holdings:
    """
    How many amplicon-carrying references each taxon has, at each rank.

    Only references that carry the amplicon count: a COI record filed in
    the 12S volume cannot answer a 12S read, so it does not make a genus
    "held" (planned item 9's inflation stays out of the expected answer).
    """

    def __init__(self, lineages: Dict[str, Dict[str, str]]):
        self.counts: Dict[str, Counter] = {column: Counter() for column in RANKS_FINEST_FIRST}
        for lineage in lineages.values():
            for column in RANKS_FINEST_FIRST:
                if lineage.get(column):
                    self.counts[column][lineage[column]] += 1

    def expected(self, lineage: Dict[str, str], removed: Dict[str, Counter]) -> Expected:
        """
        The finest rank at which the library, less `removed`, still holds
        a reference of the query's taxon (Bokulich 2018's truncation).
        """
        for column in RANKS_FINEST_FIRST:
            name = lineage.get(column, "")
            if name and self.counts[column][name] - removed.get(column, Counter())[name] > 0:
                at_or_above = RANKS_FINEST_FIRST[RANKS_FINEST_FIRST.index(column):]
                return Expected(column, name, {c: lineage[c] for c in at_or_above if lineage.get(c)})
        return Expected()


def removed_counts(lineages: Iterable[Dict[str, str]]) -> Dict[str, Counter]:
    counts: Dict[str, Counter] = {column: Counter() for column in RANKS_FINEST_FIRST}
    for lineage in lineages:
        for column in RANKS_FINEST_FIRST:
            if lineage.get(column):
                counts[column][lineage[column]] += 1
    return counts


# ---------------------------------------------------------------- judging one call


def classify(expected: Expected, call_rank: str, call_lineage: Dict[str, str]) -> str:
    """
    Which of Edgar 2018's four outcomes a call is, against what the rest of
    the library could have said.

    `expected.rank` is a lineage column ("species", "genus", ...) or "" when
    the library holds nothing of the query's taxon at any rank. `call_rank`
    is a lineage column too, or "" when the rule named nothing (no hit, too
    short, too distant, or references that disagreed). `call_lineage` holds
    the call's name at its own rank and every rank above it.

    A wrong name is looked for first, at every rank both reached: it is
    what makes a call dangerous, and a species call on the wrong genus is
    misclassified however deep it went. Only a call whose names all agree
    is judged by its depth. With nothing held to name the query by, no
    answer can be judged right or wrong, so the row is counted but left
    out of every rate - Edgar's accuracy counts only the opportunities
    "for which correctness can be determined" (Rudie, 24 September 2026).
    """
    if not expected.rank:
        return NOT_APPLICABLE
    if not call_rank:
        return UNDERCLASSIFIED
    wanted = expected.lineage or {expected.rank: expected.name}
    depth = RANKS_FINEST_FIRST.index
    both_reached = RANKS_FINEST_FIRST[max(depth(call_rank), depth(expected.rank)):]
    if any(wanted.get(c) and call_lineage.get(c) and wanted[c] != call_lineage[c] for c in both_reached):
        return MISCLASSIFIED
    if depth(call_rank) < depth(expected.rank):
        return OVERCLASSIFIED
    if depth(call_rank) > depth(expected.rank):
        return UNDERCLASSIFIED
    return CORRECT


def judge(record: str, test: str, hits: List[Dict], lineages: Dict, expected: Expected, config,
          cap_reached: bool, accepted: Dict[str, str] = None) -> Judged:
    """
    Identify one reference from the hits left to it, as stage 4 would, and
    classify the call. The vote uses the library's own labels, as stage 4's
    does; only the judging reads a species through the list's synonyms
    (`accepted`), so a record labelled *Barbatula oreas* called *Barbatula
    barbatula* is right where the list holds them to be one species.
    """
    thresholds = {k: float(v) for k, v in config.min_identity.items()}
    call = identify_sequence(hits, lineages, thresholds, float(config.min_query_coverage),
                             float(config.consensus_threshold))
    named = call.outcome == REPORTED and call.name and call.name != UNIDENTIFIED_NAME and call.rank in LABEL_COLUMN
    call_rank = LABEL_COLUMN[call.rank] if named else ""
    call_lineage = dict(call.lineage) if named else {}
    if named and call_rank and not call_lineage.get(call_rank):
        call_lineage[call_rank] = call.name
    if accepted:
        call_lineage = _accepted_lineage(call_lineage, accepted)
    tied_species = sorted({lineages[h["subject"]].lineage.get("species", "") for h in call.tied
                           if h["subject"] in lineages} - {""})
    return Judged(record, test, expected, call.name if named else "", call_rank, call_lineage,
                  classify(expected, call_rank, call_lineage), call.agreement, tied_species, len(hits),
                  cap_reached)


# ---------------------------------------------------------------- the whole check


def _accepted_lineage(lineage: Dict[str, str], accepted: Dict[str, str]) -> Dict[str, str]:
    """
    A lineage in the list's names. A synonym can move a species to another
    genus - *Pagrus auratus* is *Sparus aurata* - so the genus follows the
    accepted binomial, or a right call would disagree with it at genus.
    """
    lineage = dict(lineage)
    species = lineage.get("species", "")
    if species and accepted.get(species, species) != species:
        lineage["species"] = accepted[species]
        lineage["genus"] = accepted[species].split(" ")[0]
    return lineage


def aliases_of(species_list: SpeciesList) -> Dict[str, str]:
    """Every name the list gives - accepted or synonym - to its accepted name."""
    names = {name: name for name in species_list.accepted}
    names.update(species_list.synonyms)
    return names


def run_selfcheck(library, species_list: SpeciesList, primers: amplicons.PrimerSet, config, out_dir: Path,
                  threads: int = 1, reporter=None) -> Dict[str, object]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    table = amplicons.cut_volume(library, primers, out_dir, float(config.min_query_coverage), threads, reporter)
    rows = amplicons.read_table(table)
    cut = {r["Record"]: r for r in rows if r["Cut_By"] != amplicons.NOT_CUT}
    to_accepted = aliases_of(species_list)

    _say(reporter, f"Reading the lineages of {len(cut):,} references that carry the amplicon...")
    records = library.lookup(cut)
    # Judged in the list's names: a record under a synonym is its accepted species.
    lineage_of = {record: _accepted_lineage(found.lineage, to_accepted) for record, found in records.items()}
    holdings = Holdings(lineage_of)

    listed: Dict[str, List[str]] = defaultdict(list)          # accepted name -> its cut records
    for record, lineage in lineage_of.items():
        accepted = to_accepted.get(lineage.get("species", ""))
        if accepted:
            listed[accepted].append(record)
    uncut = Counter(to_accepted.get(r["Species"]) for r in rows
                    if r["Cut_By"] == amplicons.NOT_CUT and to_accepted.get(r["Species"]))
    if not listed:
        raise RuntimeError("no listed species has a reference that carries the amplicon")

    # One search per distinct amplicon; a query's hits serve every record carrying it.
    by_sequence: Dict[str, List[str]] = defaultdict(list)
    for records_of in listed.values():
        for record in records_of:
            by_sequence[cut[record]["Sequence"]].append(record)
    query_of = {sequence: f"q{index}" for index, sequence in enumerate(sorted(by_sequence), 1)}
    cap = max(HIT_CAP, int(config.blast_max_target_seqs))
    floor = min(float(v) for v in config.min_identity.values())
    _say(reporter, f"Searching {len(query_of):,} distinct amplicons of {len(listed):,} listed species "
                   f"against the {primers.marker} volume, keeping hits at {floor:g}% or more, up to {cap:,} each...")
    hits = _search(library, primers.marker, query_of, config, cap, threads, floor, reporter)

    subjects = {h["subject"] for group in hits.values() for h in group}
    lineages = library.lookup(subjects)
    _say(reporter, f"Judging {sum(len(v) for v in listed.values()):,} references under both tests...")

    judged: List[Tuple[str, Judged]] = []
    for accepted, records_of in sorted(listed.items()):
        own_names = {name for name, target in to_accepted.items() if target == accepted}
        species_removed = removed_counts(lineage_of[r] for r in records_of)
        for record in records_of:
            sequence = cut[record]["Sequence"]
            group = hits.get(query_of[sequence], [])
            full = len(group) >= cap
            lineage = lineage_of[record]
            one_out = [h for h in group if h["subject"] != record]
            judged.append((accepted, judge(record, LEAVE_ONE_OUT, one_out, lineages,
                                           holdings.expected(lineage, removed_counts([lineage])), config, full,
                                           to_accepted)))
            novel = [h for h in group
                     if h["subject"] not in lineages
                     or lineages[h["subject"]].lineage.get("species", "") not in own_names]
            judged.append((accepted, judge(record, NOVEL_SPECIES, novel, lineages,
                                           holdings.expected(lineage, species_removed), config, full,
                                           to_accepted)))

    return _write(out_dir, primers, library, species_list, table, rows, cut, records, judged, listed, uncut,
                  cap, config, floor)


def _search(library, marker: str, query_of: Dict[str, str], config, cap: int, threads: int,
            floor: float, reporter=None) -> Dict[str, List[Dict]]:
    """
    One search of every distinct amplicon, as stage 4 searches reads, but
    keeping only hits at or above the lowest identity threshold. A hit
    below it cannot vote (`identify_sequence`), and a list of only such
    hits yields the same call as no hits at all. It can change a call in
    one way only: by outscoring every hit above the floor over a longer
    alignment, which moves the tie. How often that happens was measured
    on a sample searched without the floor (decision 0040). The floor is
    what keeps the lists short enough for the cap not to bite.
    """
    work = Path(tempfile.mkdtemp(prefix="taxatag-selfcheck-"))
    try:
        query = work / "queries.fasta"
        query.write_text("".join(f">{q}\n{s}\n" for s, q in sorted(query_of.items(), key=lambda i: int(i[1][1:]))),
                         encoding="utf-8")
        output = work / "hits.tsv"
        searching = dataclasses.replace(config, blast_max_target_seqs=cap)
        if threads:
            searching.threads = threads
        command = _build_blast_command(searching, query, output, library.volume(marker).path)
        order = {q: n for n, q in enumerate(sorted(query_of.values(), key=lambda q: int(q[1:])), 1)}
        result = run_tool(command + ["-perc_identity", f"{floor:g}"], reporter=reporter,
                          progress=amplicons.blast_progress(output, order, "amplicons searched"),
                          progress_seconds=amplicons.PROGRESS_SECONDS, stall_seconds=amplicons.STALL_SECONDS)
        if not result.ok:
            raise RuntimeError(f"the search did not finish: {result.tail(3)}")
        return _parse_blast_results(output)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- writing it down


def _write(out_dir, primers, library, species_list, table, rows, cut, records, judged, listed, uncut, cap, config,
           floor):
    detail = out_dir / f"selfcheck_records_{primers.name}.csv"
    with open(detail, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RECORD_COLUMNS)
        writer.writeheader()
        for accepted, j in judged:
            writer.writerow({
                "Marker": primers.marker, "Locus": primers.name, "Species": accepted, "Record": j.record,
                "Source_Accession": records[j.record].source_accession if j.record in records else "",
                "Cut_By": cut[j.record]["Cut_By"], "Test": j.test,
                "Expected_Rank": RANK_LABEL.get(j.expected.rank, "nothing"), "Expected_Name": j.expected.name,
                "Call": j.call, "Call_Rank": RANK_LABEL.get(j.call_rank, ""), "Outcome": j.outcome,
                "Agreement": f"{j.agreement:.2f}" if j.call else "", "Tied_Species": " | ".join(j.tied_species),
                "Hits": j.hits, "Cap_Reached": "yes" if j.cap_reached else "",
            })

    summary_rows = []
    for accepted in sorted(listed):
        mine = [j for a, j in judged if a == accepted]
        row = {"Marker": primers.marker, "Locus": primers.name, "Species": accepted,
               "References_Cut": len(listed[accepted]), "Records_Not_Cut": uncut.get(accepted, 0)}
        for prefix, test in (("Leave_One_Out", LEAVE_ONE_OUT), ("Novel_Species", NOVEL_SPECIES)):
            counts = Counter(j.outcome for j in mine if j.test == test)
            for outcome in OUTCOMES:
                row[f"{prefix}_{_column(outcome)}"] = counts.get(outcome, 0)
        instead = Counter(j.call for j in mine if j.outcome in (OVERCLASSIFIED, MISCLASSIFIED) and j.call)
        row["Named_Instead"] = " | ".join(f"{name} ({n})" for name, n in instead.most_common())
        row["Cap_Reached"] = sum(1 for j in mine if j.cap_reached)
        summary_rows.append(row)
    per_species = out_dir / f"selfcheck_{primers.name}.csv"
    with open(per_species, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SPECIES_COLUMNS)
        writer.writeheader()
        writer.writerows(summary_rows)

    totals = {test: Counter(j.outcome for _, j in judged if j.test == test) for test in TESTS}
    settings = {
        "library": str(library.root), "locus": primers.name, "marker": primers.marker,
        "species_list": species_list.source, "amplicon_table": table.name, "identity_floor": floor,
        "amplicon_table_sha256": amplicons.checksum(rows), "hit_cap": cap,
        "thresholds": {k: float(v) for k, v in config.min_identity.items()},
        "min_query_coverage": float(config.min_query_coverage),
        "consensus_threshold": float(config.consensus_threshold),
        "cut": amplicons.summarise(rows), "totals": {t: dict(c) for t, c in totals.items()},
    }
    (out_dir / f"selfcheck_{primers.name}.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
    return {"records": detail, "species": per_species, "totals": totals, "settings": settings}


def _say(reporter, text: str) -> None:
    if reporter is not None:
        reporter.info(text)

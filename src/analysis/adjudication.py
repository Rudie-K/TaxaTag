# src/analysis/adjudication.py
"""
The sheet on which a person decides what each name means.

One row per call in the species table - every call, not only the doubtful
ones - with the evidence TaxaTag holds laid beside it: the runner-up and
how far behind it was, the ambiguity flag, how many references the library
had for the winner and its genus, and, when the user supplies a species
list for their region, whether the name is on it, under what accepted
name, and in what habitat. Then four empty columns: `Outcome`,
`Detection`, `Outcome_Species`, `Note`. TaxaTag never fills them and never
removes a row. That is the line `docs/considered.md` draws between showing
evidence and filtering on it, and a test holds it.

`Needs_Attention` says why a row deserves a look, in words, so that a
reader who only wants the doubtful ones can sort on it - and so that the
reason is on record when the decision is.

Two questions are asked of every row, and they are answered apart,
because the pilot showed they come apart: a pike named perfectly from
river water in a sea sample is a correct assignment and not a marine
fish, and a foreign goby named perfectly from DNA that was never in the
water is a correct assignment and not a detection at all. `Outcome` is
how the name did, in Bourret et al.'s (2023) words after Bokulich et al.
(2018), so that what is counted is comparable with published numbers:

    Accepted            right at the rank given, and no evidence names a lower one
    Reassigned          the wrong species; Outcome_Species says which (FP)
    Under-classified    right, but the evidence names a lower rank; Outcome_Species says which
    Unresolved          the evidence cannot judge the name

A correct genus call is *Accepted* at genus, not a failure: Bourret et al.
report precision and accuracy per rank, and a family that the marker
cannot split further is still an identification. It is *Under-classified*
only when something - a regional list, the references' own agreement - names
a rank the pipeline stopped short of. `Detection` is whether the DNA counts
for this survey, decided without reference to the name:

    genuine             from the sampled water, of a taxon the survey counts
    out of scope        from the sampled water, of a taxon the survey does not count
                        (river runoff in a marine survey; terrestrial; human)
    spurious            not from the sampled water: contamination, tag-jumping, foreign DNA

What is out of scope for one survey is the result of another, which is why
the word is not "wrong". "Method bias" - an absence the method explains -
is a word for a species that has no row, and lives in the comparison with
an independent survey, not here. `docs/decisions/0026`.

Coverage grades follow Bourret et al.: *gaps* when a congener known from
the region - one on the user's species list - has no reference in the
library, because a barcode with nothing to be ambiguous about names the
nearest relative cleanly. Without a list there is no grade: "no congeners
in the library" is trivially true of a monotypic genus (*Sardina*, 746
references, was the first thing this flagged), and TaxaTag does not know
which genera those are. `docs/science/`, sections 3, 4, 8-10;
`docs/planned.md` item 4, tier 2.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.analysis import candidates as candidates_module
from src.pipeline import layout

OUTCOMES = ("Accepted", "Reassigned", "Under-classified", "Unresolved")
DETECTIONS = ("genuine", "out of scope", "spurious")

#: Below this many references for the winning species, a species-rank
#: call is worth a look whatever else is true. One reference is one
#: specimen, one lab, one chance to be mislabelled (Locatelli et al. 2020).
FEW_REFERENCES = 2

COLUMNS = [
    "Sample", "Locus", "Marker", "ZOTU", "Reads", "Percent_Of_Sample",
    "Call", "Call_Rank", "Identity_Percent", "References_Matched", "Agreement_Percent",
    "Flag", "Runner_Up", "Runner_Up_Identity", "Bitscore_Gap_Percent",
    "Species_References", "Genus_References", "Genus_Species", "Coverage", "Missing_Congeners",
    "On_List", "Accepted_Name", "Habitat", "Tied_On_List", "Needs_Attention",
    "Outcome", "Detection", "Outcome_Species", "Note",
]

#: The columns the person fills. TaxaTag writes them empty and refuses to
#: rebuild a sheet in which any of them is not.
DECISION_COLUMNS = ("Outcome", "Detection", "Outcome_Species", "Note")

GRADE_GAPS = "gaps"                 # Bourret: unreliable due to gaps
GRADE_REPRESENTED = "represented"   # every congener the list knows from the region has references
GRADE_NONE = ""                     # not a species-rank call, or no list to judge by


# ---------------------------------------------------------------- the list


class SpeciesList:
    """
    A species list the user supplies: one accepted name per row, and
    optionally the synonyms WoRMS lists for it and its habitat.

    Read from a CSV with a `ScientificName` column (or a one-column file
    with no header); `Synonyms` ("a | b | c") and `Habitat` ("marine",
    "marine+brackish", ...) are used when present. TaxaTag takes the list
    as given and infers nothing about the region it describes - the
    scale and the source are the user's to record.
    """

    def __init__(self, names: Dict[str, str], habitat: Dict[str, str], source: str = ""):
        self.accepted = {n: n for n in names.values()}        # accepted name -> itself
        self.synonyms = {s: a for s, a in names.items() if s != a}
        self.habitat = habitat
        self.source = source

    @classmethod
    def load(cls, path: Path) -> "SpeciesList":
        names: Dict[str, str] = {}
        habitat: Dict[str, str] = {}
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            first = next(reader, None)
            if first is None:
                return cls({}, {}, str(path))
            header = [h.strip() for h in first]
            lower = [h.lower() for h in header]
            if "scientificname" in lower or "species" in lower:
                name_at = lower.index("scientificname") if "scientificname" in lower else lower.index("species")
                syn_at = lower.index("synonyms") if "synonyms" in lower else None
                hab_at = lower.index("habitat") if "habitat" in lower else None
                for row in reader:
                    if len(row) <= name_at or not row[name_at].strip():
                        continue
                    accepted = row[name_at].strip()
                    names[accepted] = accepted
                    if syn_at is not None and len(row) > syn_at:
                        for synonym in row[syn_at].split("|"):
                            synonym = synonym.strip()
                            if synonym and synonym not in names:
                                names[synonym] = accepted
                    if hab_at is not None and len(row) > hab_at:
                        habitat[accepted] = row[hab_at].strip()
            else:
                # No header: every non-empty first column is a name.
                for row in [first] + list(reader):
                    if row and row[0].strip():
                        names[row[0].strip()] = row[0].strip()
        return cls(names, habitat, str(path))

    def status(self, name: str) -> Tuple[str, str, str]:
        """(on_list, accepted_name, habitat) for a name, empty strings when absent."""
        if not name:
            return "", "", ""
        if name in self.accepted:
            return "yes", name, self.habitat.get(name, "")
        if name in self.synonyms:
            accepted = self.synonyms[name]
            return "synonym", accepted, self.habitat.get(accepted, "")
        return "no", "", ""

    def congeners(self, genus: str) -> List[str]:
        return [n for n in self.accepted if n.split(" ")[0] == genus]

    def names_for(self, accepted: str) -> List[str]:
        """The accepted name and every synonym the list gives for it - the
        names a reference library might file the species under."""
        return [accepted] + sorted(s for s, a in self.synonyms.items() if a == accepted)


# ---------------------------------------------------------------- building


def _tied_on_list(cands: List[Dict[str, str]], species_list: Optional[SpeciesList]) -> str:
    """
    Of the species the tied references name, the ones the list knows from
    the region. When a tie between two congeners has only one of them on
    the list, the list decides - Zhang et al.'s local species pool doing
    its work - and the adjudicator can see that without leaving the row.
    """
    if species_list is None:
        return ""
    names: List[str] = []
    for row in cands:
        if row["Within_Tie_Margin"] != "yes":
            break
        name = row["Candidate"]
        if " " in name and name not in names:
            names.append(name)
    on = [species_list.status(n)[1] for n in names if species_list.status(n)[0] in ("yes", "synonym")]
    return " | ".join(dict.fromkeys(on))


def _runner_up(rows: List[Dict[str, str]], call: str) -> Tuple[str, str, str]:
    """The best candidate that is a different species from the call."""
    best = float(rows[0]["Bitscore"]) if rows else 0.0
    for row in rows:
        name = row["Candidate"]
        if name and name != call and " " in name:
            gap = (best - float(row["Bitscore"])) / best * 100 if best else 0.0
            return name, row["Identity_Percent"], f"{gap:.1f}"
    return "", "", ""


def _grade(cover: Dict[str, str], genus: str, species_list: Optional[SpeciesList], library,
           cache: Dict[Tuple[str, str], int]) -> Tuple[str, str]:
    """(grade, the listed congeners with no reference), per Bourret et al."""
    if species_list is None or not genus or not cover.get("Species_References"):
        return GRADE_NONE, ""
    missing = []
    for name in species_list.congeners(genus):
        key = (cover["Marker"], name)
        if key not in cache:
            # Under any of its names: the list says Pomatoschistus flavescens,
            # the library files it as Gobiusculus flavescens, and that is not
            # a gap.
            cache[key] = sum(
                library.coverage(cover["Marker"], alias, alias.split(" ")[0])["species_references"]
                for alias in species_list.names_for(name)
            )
        if cache[key] == 0:
            missing.append(name)
    return (GRADE_GAPS if missing else GRADE_REPRESENTED), " | ".join(missing)


def _attention(row: Dict[str, str]) -> str:
    reasons = []
    if row["Flag"]:
        tied = row.get("Tied_On_List", "")
        if tied and "|" not in tied:
            reasons.append(f"ambiguous ({row['Flag']}); only {tied} is on the species list")
        else:
            reasons.append(f"ambiguous ({row['Flag']})")
    if row["Call_Rank"] == "Species":
        if row["On_List"] == "no":
            reasons.append("not on the species list")
        if row["Habitat"] and "marine" not in row["Habitat"]:
            reasons.append(f"no marine habitat ({row['Habitat']})")
        if row["Species_References"] and int(row["Species_References"]) < FEW_REFERENCES:
            reasons.append(f"{row['Species_References']} reference(s) for the species")
        if row["Coverage"] == GRADE_GAPS:
            reasons.append(f"library lacks congeners ({row['Missing_Congeners']})")
    elif row["Call_Rank"] not in ("Species", ""):
        reasons.append(f"named to {row['Call_Rank'].lower()} only")
    return "; ".join(reasons)


def build_sheet(run_dir: Path, library, species_list: Optional[SpeciesList] = None,
                top_n: int = candidates_module.TOP_N) -> List[Dict[str, str]]:
    """One row per call in the species table, evidence beside it, outcome empty."""
    results = layout.identification_dir(run_dir)
    table = candidates_module.read_table(results / "species_composition.csv")
    candidates = candidates_module.candidate_rows(run_dir, library, top_n)
    by_call: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
    for row in candidates:
        by_call.setdefault((row["Sample"], row["ZOTU"]), []).append(row)

    sheet: List[Dict[str, str]] = []
    congener_cache: Dict[Tuple[str, str], int] = {}
    for row in table:
        key = (row["Sample"], row["ZOTU"])
        cands = by_call.get(key, [])
        call = row.get("Scientific_Name", "")
        rank = row.get("Rank", "")
        flag = cands[0]["Flag"] if cands else ""
        winner = next((c for c in cands if c["Candidate"] == call), cands[0] if cands else None)
        cover = {k: (winner or {}).get(k, "") for k in ("Species_References", "Genus_References", "Genus_Species")}
        cover["Marker"] = row.get("Marker", "")
        genus = (winner or {}).get("Candidate_Genus", "") or (call.split(" ")[0] if " " in call else "")
        runner, runner_identity, gap = _runner_up(cands, call)
        on_list, accepted, habitat = species_list.status(call) if (species_list and rank == "Species") else ("", "", "")
        grade, missing = _grade(cover, genus, species_list, library, congener_cache) if rank == "Species" else (GRADE_NONE, "")
        entry = {
            "Sample": row["Sample"], "Locus": row.get("Locus", ""), "Marker": row.get("Marker", ""),
            "ZOTU": row["ZOTU"], "Reads": row.get("Reads", ""), "Percent_Of_Sample": row.get("Percent_Of_Sample", ""),
            "Call": call, "Call_Rank": rank, "Identity_Percent": row.get("Identity_Percent", ""),
            "References_Matched": row.get("References_Matched", ""), "Agreement_Percent": row.get("Agreement_Percent", ""),
            "Flag": flag, "Runner_Up": runner, "Runner_Up_Identity": runner_identity, "Bitscore_Gap_Percent": gap,
            "Species_References": cover["Species_References"] if rank == "Species" else "",
            "Genus_References": cover["Genus_References"], "Genus_Species": cover["Genus_Species"],
            "Coverage": grade, "Missing_Congeners": missing,
            "On_List": on_list, "Accepted_Name": accepted, "Habitat": habitat,
            "Tied_On_List": _tied_on_list(cands, species_list) if flag else "",
            "Needs_Attention": "", "Outcome": "", "Detection": "", "Outcome_Species": "", "Note": "",
        }
        entry["Needs_Attention"] = _attention(entry)
        sheet.append(entry)
    return sheet


def write_sheet(run_dir: Path, library, species_list: Optional[SpeciesList] = None) -> Path:
    """
    Write `06_analysis/adjudication.csv`. Refuses to overwrite a sheet that
    has outcomes in it: a filled sheet is somebody's work, and a rebuild
    must not blank it. Delete or rename it deliberately to start again.
    """
    folder = layout.analysis_dir(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "adjudication.csv"
    if path.exists() and any(r.get(c) for r in read_sheet(path) for c in DECISION_COLUMNS):
        raise FileExistsError(f"{path} already carries decisions; rename it to rebuild")
    rows = build_sheet(run_dir, library, species_list)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    sources = folder / "adjudication-sources.txt"
    counted = any(r.get("Species_References") for r in rows)
    sources.write_text(
        "Evidence on the adjudication sheet, and where it came from\n\n"
        f"run:           {run_dir}\n"
        f"library:       {getattr(library, 'root', '')}\n"
        "coverage:      " + ("counted from the library's catalogue (Species_References, Genus_References, "
                             "Genus_Species, Coverage, Missing_Congeners)\n" if counted else
                             "not counted: the run searched a raw NCBI database, which no catalogue describes, "
                             "so those columns are blank; References_Matched is the evidence of support\n")
        + f"species list:  {species_list.source if species_list else '(none given)'}\n"
        "outcomes:      " + " / ".join(OUTCOMES) + "\n"
        "detections:    " + " / ".join(DETECTIONS) + "\n"
        "filled by:     the person adjudicating - TaxaTag writes no outcome and no detection\n",
        encoding="utf-8",
    )
    return path


def read_sheet(path: Path) -> List[Dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def summarise(rows: List[Dict[str, str]]) -> Dict[str, int]:
    summary = {"calls": len(rows), "need attention": sum(1 for r in rows if r["Needs_Attention"])}
    for reason in ("ambiguous", "not on the species list", "no marine habitat", "reference(s) for the species",
                   "library lacks congeners", "named to"):
        summary[reason] = sum(1 for r in rows if reason in r["Needs_Attention"])
    return summary

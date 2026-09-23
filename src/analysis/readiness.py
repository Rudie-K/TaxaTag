# src/analysis/readiness.py
"""
Whether an analysis can be trusted on this run, said before anyone reads
its numbers. Decision 0034.

Every analysis has conditions. Some make it impossible (beta diversity
needs two samples to compare); some make it unreliable (Chao2 from fewer
than five units is only a lower bound). The literature states most of
them. The rule:

- **blocked** - impossible or meaningless here. The result is not
  produced, and the reason stands where it would have been.
- **warning** - workable, but likely not sufficient. The result is
  produced, marked, and the reason says what would help.

Every condition lives in the table below, once, with its basis - the
paper it comes from, or "TaxaTag's judgement" where the literature gives
no threshold. The terminal, the files TaxaTag writes and the window all
read this table, so they cannot disagree. A reason names the exact
cause, which is known; only the consequence is hedged.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from src.pipeline import layout
from src.pipeline.config import PipelineConfig
from src.pipeline.stage4_blast import RANK_ORDER

BLOCKED, WARNING = "blocked", "warning"

#: What a condition affects, so the window can grey out or mark the right
#: control. `NAMES` is every name the run gave, so every analysis.
NAMES, RICHNESS, DIVERSITY, BETA, SAMPLE = "names", "richness", "diversity", "beta", "sample"
SITES, CONSISTENCY, SITE_BETA, OCCURRENCE = "sites", "consistency", "site beta", "occurrence"
EFFORT = "sampling effort"

#: TaxaTag's own thresholds, where the literature gives none. Each is named
#: as a judgement wherever it is reported.
FEW_READS = 100
SHALLOW_FRACTION = 0.1

#: The fewest units Chao (1987) recommends for Chao2; from the literature, not ours.
CHAO2_UNITS = 5


@dataclass(frozen=True)
class Condition:
    code: str
    tier: str
    affects: str
    basis: str
    text: str


_TABLE = (
    Condition("run-unrecorded", WARNING, NAMES, "decisions 0027-0030 and 0036",
              "This run does not record which TaxaTag made it, so it was made by 1.0.0 or an "
              "earlier copy. Its names may carry faults fixed since; re-running it from the "
              "search stage gives current names."),
    Condition("hit-cap", WARNING, NAMES, "decision 0027; Shah et al. 2019",
              "The search kept {cap} matches per sequence (the default is now {default}). A common "
              "species can have hundreds of equally good references, so some names may rest on "
              "whichever {cap} the search happened to list first."),
    Condition("ties-at-cap", WARNING, NAMES, "decision 0027",
              "{count} call(s) had a tie as large as the search's limit, so which references voted "
              "was the search's choice; the identification audit marks them 'Tie at cap'."),
    Condition("no-lineage", WARNING, RICHNESS, "decision 0032",
              "{count} call(s) above genus on {locus} could not be checked for a species inside "
              "them, because the rows around them carry no lineage above genus. Richness may count "
              "one beside one of its own species; the taxonomy stage fills the lineage in."),
    Condition("one-unit", BLOCKED, BETA, "a comparison needs two",
              "Beta diversity needs at least two {units}; {locus} has {count}."),
    Condition("nothing-counted", BLOCKED, DIVERSITY, "no reads, no shares",
              "Nothing was counted in {subject} on {locus}, so it has no Shannon or Simpson diversity."),
    Condition("few-reads", WARNING, DIVERSITY, "TaxaTag's judgement; no published threshold",
              "Shannon and Simpson diversity for {subject} on {locus} rest on {reads} read(s), "
              "under " + str(FEW_READS) + "; treat them as rough."),
    Condition("low-depth", WARNING, SAMPLE,
              "TaxaTag's judgement; Macher et al. 2021 advise considering such samples for removal",
              "{subject} was sequenced to {reads} reads on {locus}, under a tenth of the marker's "
              "median ({median}). A shallow sample finds fewer taxa; consider leaving it out."),
    Condition("sheet-conflict", BLOCKED, SITES, "never substitute silently (interface rule 1)",
              "The sample sheet gives {subject} more than one place: {places}. Say which it belongs to."),
    Condition("one-replicate", BLOCKED, CONSISTENCY, "a comparison needs two",
              "Site {subject} on {locus} pooled one sample, so its replicates cannot be compared."),
    Condition("unequal-replicates", WARNING, SITE_BETA, "Chao et al. 2014; Baselga 2010",
              "Sites on {locus} pooled different numbers of samples ({counts}). A site with more "
              "samples finds more taxa, so compare their richness with care."),
    Condition("too-few-units", BLOCKED, EFFORT, "a curve needs two points",
              "An accumulation curve needs at least two units; {scope} on {locus} has {count}."),
    Condition("chao2-few-units", WARNING, EFFORT, "Chao 1987",
              "Chao2 for {scope} on {locus} rests on {count} units, under the five Chao (1987) "
              "recommends; read it as a lower bound that may be well below the true number."),
    Condition("beyond-double", WARNING, EFFORT, "Chao et al. 2014",
              "Site {subject} on {locus} is compared at {base} units, more than twice its {count}; "
              "its richness there is extrapolated past the range Chao et al. (2014) found reliable."),
    Condition("control-taxa", WARNING, OCCURRENCE, "Ficetola et al. 2016",
              "{taxa} turned up in a negative control as well as at a site on {locus}. They may be "
              "contamination: controls are how a contaminant is recognised."),
)

CONDITIONS: Dict[str, Condition] = {condition.code: condition for condition in _TABLE}


@dataclass(frozen=True)
class Finding:
    """One condition met on this run: where, about what, and the sentence."""

    code: str
    text: str
    locus: str = ""
    subject: str = ""

    @property
    def condition(self) -> Condition:
        return CONDITIONS[self.code]

    @property
    def tier(self) -> str:
        return self.condition.tier


def found(code: str, locus: str = "", subject: str = "", **values) -> Finding:
    """A finding for `code`, its sentence filled in. Unknown codes raise."""
    text = CONDITIONS[code].text.format(locus=locus, subject=subject, **values)
    return Finding(code, text, locus, subject)


def state(findings: List[Finding], affects: str, locus: str = "") -> Dict[str, object]:
    """
    Where one analysis stands on one marker, for the terminal and the window
    alike: blocked outright (a finding about the whole marker or run), blocked
    for some samples or sites only, and the warnings that reach it. A finding
    about every name in the run reaches every analysis.
    """
    relevant = [f for f in findings
                if f.condition.affects in (affects, NAMES) and (not f.locus or not locus or f.locus == locus)]
    return {
        "blocked": any(f.tier == BLOCKED and not f.subject for f in relevant),
        "blocked_for": sorted({f.subject for f in relevant if f.tier == BLOCKED and f.subject}),
        "warnings": [f for f in relevant if f.tier == WARNING],
    }


def status(findings: List[Finding], affects: str, locus: str = "") -> str:
    """
    What the window does with one analysis: grey it out (`blocked`), mark it
    in the warning colour with its reasons (`warning`, which includes being
    blocked for some samples only), or leave it alone (`available`).
    """
    where = state(findings, affects, locus)
    if where["blocked"]:
        return BLOCKED
    return WARNING if where["blocked_for"] or where["warnings"] else "available"


# ------------------------------------------------------------ the run itself

def _manifest(run_dir: Path) -> Optional[dict]:
    path = Path(run_dir) / "run_manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _ties_at_cap(run_dir: Path) -> int:
    path = layout.report_csv(run_dir, "identification_audit")
    if not path.exists():
        return 0
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return sum(1 for row in csv.DictReader(handle) if (row.get("Decision") or "").strip() == "Tie at cap")


def _unchecked_calls(table: List[Dict[str, str]]) -> Dict[str, int]:
    """
    Per locus, the calls above genus that the folding rule could not check:
    a coarser call beside finer calls in the same sample that carry nothing
    at the coarser call's rank.
    """
    by_sample = defaultdict(list)
    for row in table:
        by_sample[(row.get("Locus", ""), row.get("Sample", ""))].append(row)
    genus = RANK_ORDER.index("Genus")
    counts: Dict[str, int] = defaultdict(int)
    for (locus, _), rows in by_sample.items():
        for row in rows:
            rank = (row.get("Rank") or "").strip()
            if rank not in RANK_ORDER or RANK_ORDER.index(rank) >= genus:
                continue
            finer = [r for r in rows if (r.get("Rank") or "") in RANK_ORDER
                     and RANK_ORDER.index(r["Rank"]) > RANK_ORDER.index(rank)]
            if any(not (r.get(rank) or "").strip() for r in finer):
                counts[locus] += 1
    return counts


def run_findings(run_dir: Path, table: List[Dict[str, str]]) -> List[Finding]:
    """What the run's own records say about every name in it."""
    findings: List[Finding] = []
    manifest = _manifest(run_dir)
    if manifest is None or "taxatag" not in manifest:
        findings.append(found("run-unrecorded"))
    default = PipelineConfig.blast_max_target_seqs
    cap = ((manifest or {}).get("settings") or {}).get("runtime", {}).get("blast_max_target_seqs")
    if isinstance(cap, int) and cap < default:
        findings.append(found("hit-cap", cap=cap, default=default))
    ties = _ties_at_cap(run_dir)
    if ties:
        findings.append(found("ties-at-cap", count=ties))
    for locus, count in sorted(_unchecked_calls(table).items()):
        findings.append(found("no-lineage", locus=locus, count=count))
    return findings


def check(run_dir: Path, sheet=None, basis: str = "mixed", keep_contaminants: bool = False) -> List[Finding]:
    """
    Every finding for this run - and for its sites, given a sample sheet -
    without writing anything. What `python -m src.analysis check` prints
    and what the Analysis tab will read to grey out and mark its options.
    """
    from src.analysis import diversity, effort, sites  # here, not above: they import this module

    findings = list(diversity.describe(run_dir, basis, keep_contaminants)["findings"])
    if sheet is not None:
        findings += sites.pool(run_dir, sheet, basis, keep_contaminants)["findings"]
    findings += effort.plan(run_dir, sheet, basis, keep_contaminants)["findings"]
    # Each analysis repeats what the run says about its own names; list it once.
    once, seen = [], set()
    for finding in findings:
        if finding not in seen:
            seen.add(finding)
            once.append(finding)
    return once


def notes_section(findings: List[Finding]) -> str:
    """The findings as the notes file states them: blocked first, each with its basis."""
    if not findings:
        return "Cautions: none.\n"
    lines = ["Cautions (" + str(len(findings)) + "):"]
    for finding in sorted(findings, key=lambda f: (f.tier != BLOCKED, f.locus, f.subject, f.code)):
        mark = "[blocked]" if finding.tier == BLOCKED else "[!]"
        lines.append(f"  {mark} {finding.text} ({finding.code}; basis: {finding.condition.basis})")
    return "\n".join(lines) + "\n"

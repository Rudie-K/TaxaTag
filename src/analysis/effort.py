# src/analysis/effort.py
"""
Was sampling enough? Step 3 of the Analysis tab's tier 1 (`docs/planned.md`
item 4), decided in `docs/decisions/0035`, with its conditions under 0034.

Answered from incidence - which units found which taxa - because TaxaTag's
reads cannot say what was missed and its units can (sections 19 and 23 of
`docs/science/what-the-literature-says.md`):

- **The curve** (Colwell et al. 2012, Eqs. 17-18; Chao et al. 2014,
  Appendix C): the expected number of taxa in t of the T units, exact below
  T (C.2), extrapolated to twice T (C.4) and no further - beyond double,
  extrapolated richness is biased.
- **Chao2** (Chao 1987; C.5): a lower bound on the taxa present, from the
  taxa found in exactly one unit (uniques) and in exactly two (duplicates).
- **Coverage** (C.7, C.9, C.10; Appendix G's form when there are no
  duplicates): the estimated share of the assemblage's incidence that
  belongs to taxa already found.
- **Intervals** (Appendix G): 200 draws from a bootstrap assemblage, with a
  fixed seed so the same run gives the same interval; estimate +/- 1.96
  standard errors.
- **Units** (section 23): for the survey, sites when a sample sheet is given
  and samples otherwise; for one site, its replicates.
- **Sites compared at one number of units** (Chao et al. 2014, Box 1): the
  larger of the most replicates any site has and the fewest doubled.

Taxa are counted as steps 1 and 2 count them (decision 0032, contaminants
set aside), across each scope, so a site's curve ends at the richness
`sites.csv` gives it.
"""

from __future__ import annotations

import math
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

import numpy as np

from src.analysis import diversity, readiness, sites
from src.pipeline import layout

#: Chao et al. 2014, Appendix G: 200 replicates gave stable intervals.
BOOTSTRAPS = 200
#: Any fixed number would do; fixed, so the same run gives the same interval.
SEED = 2014
Z = 1.96

RAREFIED, OBSERVED, EXTRAPOLATED = "rarefied", "observed", "extrapolated"
SURVEY = "the survey"

CURVE_COLUMNS = ["Locus", "Marker", "Scope", "Unit", "Units", "Method",
                 "Richness", "Richness_Lower", "Richness_Upper",
                 "Coverage", "Coverage_Lower", "Coverage_Upper"]
SUMMARY_COLUMNS = ["Locus", "Marker", "Scope", "Unit", "Units", "Observed_Richness",
                   "Uniques", "Duplicates", "Chao2", "Chao2_Lower", "Chao2_Upper",
                   "Coverage", "Coverage_Lower", "Coverage_Upper", "Caution"]
COMPARISON_COLUMNS = ["Locus", "Marker", "Site", "Replicates", "Base_Units", "Method",
                      "Richness_At_Base", "Richness_Lower", "Richness_Upper", "Coverage_At_Base", "Caution"]


# ------------------------------------------------------------- the formulas

def _q0(uniques: int, duplicates: int, units: int) -> float:
    """Taxa present but found in no unit, by Chao2 (C.5; Colwell et al. 2012, Eqs. 21-22)."""
    k = (units - 1) / units
    if duplicates > 0:
        return k * uniques * uniques / (2 * duplicates)
    return k * uniques * (uniques - 1) / 2


def _bracket(uniques: int, duplicates: int, units: int) -> float:
    """
    The factor in sample coverage (C.7, C.10). With no duplicates, the form
    Appendix G gives for the reference sample - used for the extrapolated
    coverage too, so the extrapolated curve starts where the observed ends.
    """
    if duplicates > 0:
        return (units - 1) * uniques / ((units - 1) * uniques + 2 * duplicates)
    if uniques > 0:
        return (units - 1) * (uniques - 1) / ((units - 1) * (uniques - 1) + 2)
    return 0.0


def _log_factorials(n: int) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum(np.log(np.arange(1, n + 1)))])


def estimate(frequencies: Sequence[int], units: int, points: Sequence[int]) -> Dict[str, object]:
    """
    From incidence frequencies - for each taxon, how many of the `units` it
    was found in - the expected richness and coverage at each number of
    units in `points`, with Chao2 and the coverage of all `units`.
    """
    y = np.asarray([f for f in frequencies if f > 0], dtype=np.int64)
    observed = int(y.size)
    uniques, duplicates, incidences = int((y == 1).sum()), int((y == 2).sum()), int(y.sum())
    q0 = _q0(uniques, duplicates, units)
    bracket = _bracket(uniques, duplicates, units)
    coverage = 1.0 - (uniques / incidences) * bracket if incidences else None
    lf = _log_factorials(units)
    richness_at, coverage_at, method = [], [], []
    for t in points:
        if t < units:
            found = (units - y) >= t
            left = units - y[found]
            # C.2 is C(T-Y, t)/C(T, t) and C.9 is C(T-Y, t)/C(T-1, t): logs, so large T cannot overflow.
            shared = lf[left] - lf[left - t]
            richness_at.append(observed - float(np.exp(shared - lf[units] + lf[units - t]).sum()))
            coverage_at.append(1.0 - float(((y[found] / incidences) *
                                            np.exp(shared - lf[units - 1] + lf[units - 1 - t])).sum())
                               if incidences else None)
            method.append(RAREFIED)
        elif t == units:
            richness_at.append(float(observed))
            coverage_at.append(coverage)
            method.append(OBSERVED)
        else:
            beyond = t - units
            if q0 > 0 and uniques > 0:
                richness_at.append(observed + q0 * (1.0 - (1.0 - uniques / (uniques + units * q0)) ** beyond))
            else:
                richness_at.append(float(observed))
            coverage_at.append(1.0 - (uniques / incidences) * bracket ** (beyond + 1) if incidences else None)
            method.append(EXTRAPOLATED)
    return {"units": units, "observed": observed, "uniques": uniques, "duplicates": duplicates,
            "incidences": incidences, "q0": q0, "chao2": observed + q0, "coverage": coverage,
            "richness_at": richness_at, "coverage_at": coverage_at, "method": method}


def _assemblage(frequencies: Sequence[int], units: int, est: Dict[str, object]) -> np.ndarray:
    """
    Appendix G's bootstrap assemblage: each detected taxon at an adjusted
    incidence probability, and ceil(Q0) undetected ones sharing what the
    coverage says is missing.
    """
    y = np.asarray([f for f in frequencies if f > 0], dtype=float)
    if not est["incidences"]:
        return np.zeros(0)
    missing = (est["incidences"] / units) * (1.0 - est["coverage"])
    weights = (y / units) * (1.0 - y / units) ** units
    tau = missing / weights.sum() if weights.sum() > 0 else 0.0
    detected = (y / units) * (1.0 - tau * (1.0 - y / units) ** units)
    hidden = math.ceil(round(est["q0"], 9))
    undetected = np.full(hidden, min(1.0, missing / hidden)) if hidden > 0 else np.zeros(0)
    return np.clip(np.concatenate([detected, undetected]), 0.0, 1.0)


def bootstrap(frequencies: Sequence[int], units: int, points: Sequence[int], est: Dict[str, object],
              rng: np.random.Generator, replicates: int = BOOTSTRAPS) -> Dict[str, object]:
    """Standard errors of richness and coverage at each point, and of Chao2, by Appendix G."""
    probabilities = _assemblage(frequencies, units, est)
    richness, coverage, chao2 = [], [], []
    for _ in range(replicates):
        again = estimate(rng.binomial(units, probabilities), units, points)
        richness.append(again["richness_at"])
        coverage.append([np.nan if c is None else c for c in again["coverage_at"]])
        chao2.append(again["chao2"])
    with np.errstate(invalid="ignore"):
        return {"richness": np.std(richness, axis=0, ddof=1),
                "coverage": np.nanstd(np.asarray(coverage, dtype=float), axis=0, ddof=1),
                "chao2": float(np.std(chao2, ddof=1))}


def _interval(value, se, low: float = 0.0, high: Optional[float] = None):
    """Estimate +/- 1.96 standard errors, kept within what the quantity can be."""
    if value is None or se is None or np.isnan(se):
        return None, None
    lower, upper = max(low, value - Z * se), value + Z * se
    return lower, (min(high, upper) if high is not None else upper)


# ---------------------------------------------------------------- the units

def frequencies(units: Dict[str, Set[diversity.Taxon]], lineage, basis: str = diversity.MIXED) -> List[int]:
    """
    How many units found each taxon, the taxa counted across all the units
    together - so a genus call is folded when any unit named a species of it.
    """
    pooled = set().union(*units.values()) if units else set()
    folded = diversity.folded_away(pooled, lineage) if basis == diversity.MIXED else set()
    return [sum(taxon in found for found in units.values()) for taxon in sorted(pooled - folded)]


def base_units(sizes) -> int:
    """
    The one number of units every site is compared at: Chao et al. 2014,
    Box 1, with r = 2 - the larger of the most units any site has and the
    fewest doubled, so no site's data is thrown away and no site is
    extrapolated further than necessary.
    """
    sizes = list(sizes)
    return max(max(sizes), min(2 * n for n in sizes))


def plan(run_dir: Path, sheet=None, basis: str = diversity.MIXED,
         keep_contaminants: bool = False) -> Dict[str, object]:
    """
    What step 3 would compute and what it would warn or block, without the
    arithmetic: the scopes - the survey, and each site given a sheet - with
    their units, and each marker's base number of units for comparing sites.
    """
    table = diversity.read_table(run_dir)
    sequenced = diversity.sequenced_samples(run_dir, table)
    rows_of: Dict[tuple, List[Dict[str, str]]] = defaultdict(list)
    markers: Dict[str, str] = {}
    for row in table:
        rows_of[(row["Locus"], row["Sample"])].append(row)
        markers[row["Locus"]] = row.get("Marker", "")
    if sheet is not None:
        sites.check(sheet, set().union(*sequenced.values()) if sequenced else set())

    # What the run says about its own names reaches every analysis (0034).
    scopes, findings, bases = [], readiness.run_findings(run_dir, table), {}
    for locus in sorted(sequenced):
        marker = markers.get(locus, "")
        calls, lineage = {}, {}
        for sample in sorted(sequenced[locus]):
            found, their_lineage, _, _ = diversity._calls(rows_of.get((locus, sample), []), basis, keep_contaminants)
            calls[sample] = set(found)
            lineage.update(their_lineage)

        if sheet is None:
            scopes.append(dict(locus=locus, marker=marker, scope=SURVEY, subject="", unit="sample",
                               units=calls, lineage=lineage))
            continue
        replicates_of: Dict[str, List[str]] = defaultdict(list)
        for sample in sorted(calls):
            if sample in sheet.site_of:
                replicates_of[sheet.site_of[sample]].append(sample)
        names = sorted(replicates_of, key=sites._natural)
        scopes.append(dict(locus=locus, marker=marker, scope=SURVEY, subject="", unit="site", lineage=lineage,
                           units={site: set().union(*(calls[s] for s in replicates_of[site])) for site in names}))
        for site in names:
            scopes.append(dict(locus=locus, marker=marker, scope=f"site {site}", subject=site, unit="replicate",
                               lineage=lineage, units={s: calls[s] for s in replicates_of[site]}))

        sizes = {site: len(replicates_of[site]) for site in names if len(replicates_of[site]) >= 2}
        if sizes:
            base = base_units(sizes.values())
            bases[locus] = base
            for site, n in sizes.items():
                if base > 2 * n:
                    findings.append(readiness.found("beyond-double", locus, site, base=base, count=n))

    for scope in scopes:
        n = len(scope["units"])
        if n < 2:
            findings.append(readiness.found("too-few-units", scope["locus"], scope["subject"],
                                            scope=scope["scope"], count=n))
        elif n < readiness.CHAO2_UNITS:
            findings.append(readiness.found("chao2-few-units", scope["locus"], scope["subject"],
                                            scope=scope["scope"], count=n))
    return {"scopes": scopes, "findings": findings, "bases": bases}


def _rng(locus: str, scope: str, seed: int) -> np.random.Generator:
    """One generator per scope, so adding a site does not move another site's interval."""
    return np.random.default_rng([seed, zlib.crc32(f"{locus}|{scope}".encode("utf-8"))])


def describe(run_dir: Path, sheet=None, basis: str = diversity.MIXED, keep_contaminants: bool = False,
             seed: int = SEED) -> Dict[str, object]:
    """Every table step 3 writes, as rows, and every finding, without writing anything."""
    planned = plan(run_dir, sheet, basis, keep_contaminants)
    curve, summary, comparison = [], [], []

    def cautions_for(locus: str, subject: str, codes) -> str:
        return "; ".join(f.code for f in planned["findings"]
                         if f.locus == locus and f.subject == subject and f.code in codes)

    for scope in planned["scopes"]:
        units = len(scope["units"])
        key = {"Locus": scope["locus"], "Marker": scope["marker"], "Scope": scope["scope"], "Unit": scope["unit"]}
        caution = cautions_for(scope["locus"], scope["subject"], ("too-few-units", "chao2-few-units"))
        counts = frequencies(scope["units"], scope["lineage"], basis)
        if units < 2:
            summary.append({**key, "Units": units, "Observed_Richness": len(counts), "Caution": caution})
            continue
        points = list(range(1, 2 * units + 1))
        est = estimate(counts, units, points)
        se = bootstrap(counts, units, points, est, _rng(scope["locus"], scope["scope"], seed))
        for i, t in enumerate(points):
            low, high = _interval(est["richness_at"][i], se["richness"][i])
            c_low, c_high = _interval(est["coverage_at"][i], se["coverage"][i], 0.0, 1.0)
            curve.append({**key, "Units": t, "Method": est["method"][i],
                          "Richness": est["richness_at"][i], "Richness_Lower": low, "Richness_Upper": high,
                          "Coverage": est["coverage_at"][i], "Coverage_Lower": c_low, "Coverage_Upper": c_high})
        chao_low, chao_high = _interval(est["chao2"], se["chao2"], low=float(est["observed"]))
        c_low, c_high = _interval(est["coverage"], se["coverage"][units - 1], 0.0, 1.0)
        summary.append({**key, "Units": units, "Observed_Richness": est["observed"],
                        "Uniques": est["uniques"], "Duplicates": est["duplicates"],
                        "Chao2": est["chao2"], "Chao2_Lower": chao_low, "Chao2_Upper": chao_high,
                        "Coverage": est["coverage"], "Coverage_Lower": c_low, "Coverage_Upper": c_high,
                        "Caution": caution})

    for scope in planned["scopes"]:
        base = planned["bases"].get(scope["locus"])
        units = len(scope["units"])
        if not scope["subject"] or base is None or units < 2:
            continue
        counts = frequencies(scope["units"], scope["lineage"], basis)
        est = estimate(counts, units, [base])
        se = bootstrap(counts, units, [base], est, _rng(scope["locus"], scope["scope"] + " at base", seed))
        low, high = _interval(est["richness_at"][0], se["richness"][0])
        comparison.append({"Locus": scope["locus"], "Marker": scope["marker"], "Site": scope["subject"],
                           "Replicates": units, "Base_Units": base, "Method": est["method"][0],
                           "Richness_At_Base": est["richness_at"][0], "Richness_Lower": low,
                           "Richness_Upper": high, "Coverage_At_Base": est["coverage_at"][0],
                           "Caution": cautions_for(scope["locus"], scope["subject"], ("beyond-double",))})

    return {"curve": curve, "summary": summary, "sites": comparison,
            "findings": planned["findings"], "bases": planned["bases"]}


NOTES = """TaxaTag sampling effort, step 3 - how these numbers were made
Units: {unit_text}
Taxa: counted across each scope as steps 1 and 2 count them (decision 0032,
  basis {basis}); contaminants {contaminant_text}.
Curve (effort-curve{suffix}.csv): the expected number of taxa in t of the T
  units - exact below T (Chao et al. 2014, Appendix C, C.2; Colwell et al.
  2012, Eq. 17), observed at T, and extrapolated to twice T (C.4) and no
  further: beyond double, extrapolated richness is biased (Chao et al. 2014).
Chao2 (effort-summary{suffix}.csv): observed + ((T-1)/T) Q1^2 / (2 Q2), or
  ((T-1)/T) Q1 (Q1-1) / 2 when Q2 = 0 (Chao 1987; C.5), with Q1 = Uniques
  (taxa in one unit) and Q2 = Duplicates (in two). A lower bound on the
  taxa present, not an estimate of them.
Coverage: the estimated share of the assemblage's incidence that belongs to
  taxa already found - C.7 at T, C.9 below it, C.10 beyond; with no
  duplicates, the form of Appendix G. What is missing is 1 - Coverage.
Intervals: 95%, the estimate +/- 1.96 bootstrap standard errors, from {bootstraps}
  draws of Appendix G's bootstrap assemblage, seed {seed}. Richness is kept at
  or above 0, Chao2 at or above the observed richness, coverage within 0-1.
{sites_text}Not estimated: anything from reads. Every taxon carries at least the run's
  minimum ZOTU size in reads, so read-based estimators would report every
  sample complete (section 19 of TaxaTag's literature file).
Decision records: TaxaTag docs/decisions/0034 and 0035.
{cautions}"""


def write_effort(run_dir: Path, sheet=None, basis: str = diversity.MIXED, keep_contaminants: bool = False,
                 out_dir: Optional[Path] = None, seed: int = SEED) -> Dict[str, object]:
    """Write step 3's tables and notes into the run's `06_analysis/` (or `out_dir`)."""
    tables = describe(run_dir, sheet, basis, keep_contaminants, seed)
    folder = Path(out_dir) if out_dir else layout.analysis_dir(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    suffix = "" if basis == diversity.MIXED else f"-{basis}"
    written = {
        "curve": diversity._write(folder / f"effort-curve{suffix}.csv", CURVE_COLUMNS, tables["curve"]),
        "summary": diversity._write(folder / f"effort-summary{suffix}.csv", SUMMARY_COLUMNS, tables["summary"]),
    }
    if sheet is not None:
        written["sites"] = diversity._write(folder / f"effort-sites{suffix}.csv", COMPARISON_COLUMNS, tables["sites"])
        unit_text = ("for the survey, each site of the sample sheet; for a site, its replicates,\n"
                     "  which the curve treats as independent samples of the site - PCR replicates\n"
                     "  of one water sample are not (section 23).")
        bases = ", ".join(f"{locus}: {base}" for locus, base in sorted(tables["bases"].items())) or "none"
        sites_text = ("Sites compared (effort-sites" + suffix + ".csv) at one number of units per marker,\n"
                      "  the larger of the most replicates a site has and the fewest doubled (Chao et\n"
                      f"  al. 2014, Box 1): {bases}. A site with one replicate is left out.\n")
    else:
        unit_text = ("each sample. If the samples are replicates of sites, give a sample sheet:\n"
                     "  replicates of one site are not independent draws of the survey (section 23).")
        sites_text = ""
    notes = folder / f"effort{suffix}-notes.txt"
    notes.write_text(NOTES.format(
        unit_text=unit_text, basis=basis, suffix=suffix, bootstraps=BOOTSTRAPS, seed=seed,
        contaminant_text="kept, as asked" if keep_contaminants else "set aside",
        sites_text=sites_text, cautions=readiness.notes_section(tables["findings"]),
    ), encoding="utf-8", newline="\n")
    written["notes"] = notes
    return {"written": written, "tables": tables, "findings": tables["findings"]}

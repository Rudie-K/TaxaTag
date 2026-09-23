# src/pipeline/stage5_taxonomy.py
"""
Stage 5: Attaching full taxonomy.

A database match gives a species name and an accession number, but ecologists
need the whole lineage - kingdom down to species - to group results, drop
non-target hits, and compare against a species list for the region.

This stage asks NCBI for the lineage behind each match. Results are cached in
a file that outlives the run, so the second and later runs on overlapping data
resolve almost everything without touching the network at all.

Failure here is not fatal. If NCBI is unreachable the run still ends with a
usable species table from the previous stage, just without lineage columns.
"""

from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.pipeline import layout
from src.pipeline.config import PipelineConfig
from src.utils.accessions import normalise_accession
# The rank stage 4 gives a sequence that matched real references which agree
# on no name. Imported rather than repeated so the two stages cannot drift.
from src.pipeline.stage4_blast import UNIDENTIFIED_RANK
from src.utils.reporting import Reporter, console_reporter

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

#: NCBI allows 3 requests per second without an API key.
REQUEST_DELAY = 0.34
REQUEST_TIMEOUT = 30

#: NCBI asks that no more than a few hundred ids go in one request.
BATCH_SIZE = 150

#: The ranks reported, in the order a biologist reads them.
RANKS = ("kingdom", "phylum", "class", "order", "family", "genus", "species")


@dataclass
class TaxonomyRecord:
    """One organism's position in the tree of life."""

    taxid: str = ""
    scientific_name: str = ""
    rank: str = ""
    lineage: Dict[str, str] = field(default_factory=dict)

    def column_values(self) -> Dict[str, str]:
        values = {
            "NCBI_TaxID": self.taxid,
            "NCBI_Scientific_Name": self.scientific_name,
            "NCBI_Rank": self.rank,
        }
        for rank in RANKS:
            values[rank.capitalize()] = self.lineage.get(rank, "")
        values["Taxonomy_Status"] = "Resolved" if self.taxid else "Unresolved"
        return values


EMPTY_RECORD = TaxonomyRecord()


class TaxonomyCache:
    """A taxid -> lineage store on disk, shared by every run."""

    def __init__(self, path: Path, reporter: Reporter):
        self.path = Path(path)
        self.reporter = reporter
        self.records: Dict[str, TaxonomyRecord] = {}
        #: accession -> taxid, so a repeat accession skips a lookup too.
        self.accessions: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            # A cache is an optimisation. A damaged one is rebuilt silently
            # rather than stopping a run.
            self.reporter.debug("Taxonomy cache could not be read; starting a new one.")
            return

        for taxid, record in data.get("taxa", {}).items():
            self.records[taxid] = TaxonomyRecord(**record)
        self.accessions = data.get("accessions", {})
        if self.records:
            self.reporter.info(f"Reusing {len(self.records)} cached taxonomy records.")

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "taxa": {k: asdict(v) for k, v in sorted(self.records.items())},
                        "accessions": self.accessions,
                    },
                    handle,
                    indent=2,
                )
        except OSError as error:
            self.reporter.warning(f"Taxonomy cache could not be saved: {error}")


class NCBIClient:
    """A polite Entrez client: identifies itself, backs off, and retries."""

    def __init__(self, config: PipelineConfig):
        self.email = config.ncbi_email
        self.tool = config.ncbi_tool
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": f"{self.tool} ({self.email or 'no-email-configured'})"}
        )
        self.session.mount(
            "https://",
            HTTPAdapter(
                max_retries=Retry(
                    total=4,
                    backoff_factor=1,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET"],
                )
            ),
        )

    def _get(self, endpoint: str, **params) -> requests.Response:
        params.update({"tool": self.tool, "email": self.email})
        response = self.session.get(
            f"{EUTILS_BASE}/{endpoint}", params=params, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        time.sleep(REQUEST_DELAY)
        return response

    def accessions_to_taxids(self, accessions: List[str]) -> Dict[str, str]:
        """Look up which organism each sequence accession belongs to."""
        response = self._get(
            "esummary.fcgi", db="nuccore", id=",".join(accessions), retmode="json"
        )
        result = response.json().get("result", {})
        mapping = {}
        for uid in result.get("uids", []):
            record = result.get(uid, {})
            accession = record.get("accessionversion")
            taxid = record.get("taxid")
            if accession and taxid:
                mapping[accession] = str(taxid)
        return mapping

    def taxids_to_lineages(self, taxids: List[str]) -> Dict[str, TaxonomyRecord]:
        """Fetch the full lineage for each taxid."""
        response = self._get("efetch.fcgi", db="taxonomy", id=",".join(taxids), retmode="xml")
        root = ET.fromstring(response.text)

        records: Dict[str, TaxonomyRecord] = {}
        for taxon in root.findall("Taxon"):
            taxid = taxon.findtext("TaxId", "")
            name = taxon.findtext("ScientificName", "")
            rank = taxon.findtext("Rank", "")

            lineage: Dict[str, str] = {}
            lineage_node = taxon.find("LineageEx")
            if lineage_node is not None:
                for ancestor in lineage_node.findall("Taxon"):
                    ancestor_rank = (ancestor.findtext("Rank", "") or "").lower()
                    # NCBI files animals and plants under "superkingdom" or
                    # "clade" rather than a plain "kingdom" rank.
                    if ancestor_rank in ("superkingdom", "kingdom"):
                        lineage.setdefault("kingdom", ancestor.findtext("ScientificName", ""))
                    elif ancestor_rank in RANKS:
                        lineage[ancestor_rank] = ancestor.findtext("ScientificName", "")

            if rank.lower() == "species":
                lineage["species"] = name

            records[taxid] = TaxonomyRecord(
                taxid=taxid, scientific_name=name, rank=rank, lineage=lineage
            )
        return records


#: Lineage columns a reference library fills in directly.
LIBRARY_LINEAGE_COLUMNS = ["Kingdom", "Phylum", "Class", "Order", "Family", "Genus", "Species"]


def _rows_with_lineage(table: "pd.DataFrame") -> int:
    """
    How many rows already carry a lineage.

    A row counts as resolved when any rank above species is filled in. Species
    alone is not enough, because a hit named only by its accession would
    otherwise look complete.
    """
    present = [c for c in LIBRARY_LINEAGE_COLUMNS[:-1] if c in table.columns]
    if not present:
        return 0
    filled = table[present].apply(
        lambda row: any(str(value).strip() for value in row), axis=1
    )
    return int(filled.sum())


def _batched(items: List[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def run_stage5(config: PipelineConfig, reporter: Optional[Reporter] = None) -> dict:
    """Add lineage columns to the species table produced by the previous stage."""
    reporter = reporter or console_reporter()
    reporter.heading(layout.STAGE_TITLES["taxonomy"])

    species_csv = layout.identification_dir(config.run_dir) / layout.FINAL_SPECIES_TABLE
    if not species_csv.exists():
        return {
            "status": "error",
            "message": "No species table found. Run the previous stage first.",
            "taxonomy_csv": None,
        }

    # keep_default_na stops pandas turning text such as "NA", "N/A" or "None"
    # into missing values. Those are real strings here: a database with no
    # taxonomy writes "N/A", and there are genuine taxon and sample names that
    # pandas would otherwise silently blank.
    table = pd.read_csv(species_csv, keep_default_na=False, dtype=str)
    if table.empty:
        return {
            "status": "error",
            "message": "The species table is empty, so there is no taxonomy to look up.",
            "taxonomy_csv": None,
        }

    # A local reference library already carries the full lineage for every
    # sequence it holds, so there is nothing to ask NCBI for. Skipping the
    # network entirely is what makes an offline run possible.
    already_resolved = _rows_with_lineage(table)
    if already_resolved == len(table):
        output_csv = layout.identification_dir(config.run_dir) / layout.FINAL_TAXONOMY_TABLE
        table.to_csv(output_csv, index=False)
        reporter.success(
            f"All {len(table)} records already carry a full lineage from the "
            "reference library. No lookup was needed."
        )
        return {
            "status": "ok",
            "message": f"Taxonomy for {len(table)} records came from the reference library.",
            "taxonomy_csv": output_csv,
            "resolved": int(already_resolved),
            "total": len(table),
            "source": "reference library",
        }
    if already_resolved:
        reporter.info(
            f"{already_resolved} of {len(table)} records already have a lineage; "
            "looking up the rest at NCBI."
        )

    cache = TaxonomyCache(config.taxonomy_cache, reporter)

    # BLAST supplies the taxid directly when the database carries one, which
    # saves an entire round trip per accession.
    table["_accession"] = table["Accession"].map(normalise_accession)

    # A record the previous stage could not name must not be named here.
    #
    # When equally-good references disagree, stage 4 reports the sequence as
    # Unidentified and leaves its lineage empty - that is decision 0003, and
    # the case it was built for is a 199-base 16S sequence matching ten
    # references at 100% identity that agree on no name at all. It still
    # carries an Accession, because knowing which reference it matched is
    # useful. That accession is only the best-scoring of the ten (decision
    # 0036), no more the answer than any other.
    #
    # Looking that accession up undoes the whole decision one stage later. On
    # a real run it named 81 of 82 unidentified records, and made a Chinese
    # land snail the most abundant organism in a Sussex sea-water dataset:
    # 2.7 million reads, three times the next taxon. The consensus had
    # refused, correctly, and this handed the answer back.
    #
    # Cleared here rather than filtered later so nothing downstream can pick
    # it up by accident.
    unnamed = table["Rank"].astype(str).str.strip() == UNIDENTIFIED_RANK
    if unnamed.any():
        table.loc[unnamed, "_accession"] = ""
        if "TaxID" in table.columns:
            table.loc[unnamed, "TaxID"] = ""
        reporter.info(
            f"{int(unnamed.sum())} sequence(s) matched references that agree on "
            "no name. They are reported as unidentified and are not looked up: "
            "naming them from one reference would undo that."
        )
    # BLAST can report several taxids separated by semicolons when a sequence
    # is shared between organisms; the first is the representative one.
    table["_taxid"] = (
        table.get("TaxID", pd.Series([""] * len(table), index=table.index))
        .astype(str)
        .str.split(";").str[0]
        .str.strip()
        .replace({"nan": "", "0": "", "N/A": ""})
    )

    known_taxids = {t for t in table["_taxid"] if t}
    accessions_needing_taxid = sorted(
        {
            accession
            for accession, taxid in zip(table["_accession"], table["_taxid"])
            if accession and not taxid and accession not in cache.accessions
        }
    )

    client = NCBIClient(config)
    network_failed = False

    # --- Step 1: accession -> taxid, for anything BLAST did not tell us ---
    if accessions_needing_taxid:
        reporter.info(f"Looking up {len(accessions_needing_taxid)} accession(s) at NCBI.")
        for index, batch in enumerate(_batched(accessions_needing_taxid, BATCH_SIZE), start=1):
            reporter.checkpoint()
            total_batches = -(-len(accessions_needing_taxid) // BATCH_SIZE)
            reporter.progress(index / (total_batches * 2), "Looking up accessions")
            try:
                cache.accessions.update(client.accessions_to_taxids(batch))
            except (requests.RequestException, ValueError) as error:
                reporter.warning(f"NCBI lookup failed: {error}")
                network_failed = True
                break
    elif not known_taxids:
        reporter.info("Nothing to look up.")

    # Fill in taxids we now know from accessions.
    table["_taxid"] = [
        taxid or cache.accessions.get(accession, "")
        for taxid, accession in zip(table["_taxid"], table["_accession"])
    ]
    wanted_taxids = sorted({t for t in table["_taxid"] if t})
    missing_taxids = [t for t in wanted_taxids if t not in cache.records]

    # --- Step 2: taxid -> lineage ---------------------------------------
    if missing_taxids and not network_failed:
        reporter.info(f"Fetching {len(missing_taxids)} lineage(s) from NCBI.")
        total_batches = -(-len(missing_taxids) // BATCH_SIZE)
        for index, batch in enumerate(_batched(missing_taxids, BATCH_SIZE), start=1):
            reporter.checkpoint()
            reporter.progress(0.5 + index / (total_batches * 2), "Fetching taxonomy")
            try:
                cache.records.update(client.taxids_to_lineages(batch))
            except (requests.RequestException, ET.ParseError) as error:
                reporter.warning(f"NCBI taxonomy fetch failed: {error}")
                network_failed = True
                break

    cache.save()

    # --- Step 3: widen the table ----------------------------------------
    taxonomy_columns = [
        cache.records.get(taxid, EMPTY_RECORD).column_values()
        for taxid in table["_taxid"]
    ]
    base = table.drop(columns=["_accession", "_taxid"]).reset_index(drop=True)
    fetched = pd.DataFrame(taxonomy_columns).reset_index(drop=True)

    # Some rows may already carry a lineage from a reference library. Those
    # values win: they were curated for this marker, whereas the NCBI lookup
    # is a fallback. Merging rather than concatenating also avoids ending up
    # with two columns both called "Family".
    shared = [column for column in fetched.columns if column in base.columns]
    for column in shared:
        existing = base[column].astype(str).str.strip()
        base[column] = existing.where(existing != "", fetched[column])

    enriched = pd.concat([base, fetched.drop(columns=shared)], axis=1)

    # Taxonomy_Status arrives describing the NCBI lookup alone, because that is
    # all it could know before the merge above. Left that way it calls every
    # record whose lineage came from the reference library "Unresolved" - and
    # on a run against a local library that is nearly all of them. It said
    # "1557 record(s) could not be resolved" on a table where 1541 of those
    # 1557 carried a full lineage, which reads as a failed run and is not one.
    #
    # Rewritten here, after the merge, to describe the row rather than the
    # lookup: where the lineage came from, or that there is none.
    lineage_columns = [rank.capitalize() for rank in RANKS if rank.capitalize() in enriched.columns]
    has_lineage = (
        enriched[lineage_columns].astype(str).apply(lambda c: c.str.strip() != "").any(axis=1)
        if lineage_columns else pd.Series(False, index=enriched.index)
    )
    from_ncbi = enriched["Taxonomy_Status"] == "Resolved"
    enriched["Taxonomy_Status"] = "Unresolved"
    enriched.loc[has_lineage, "Taxonomy_Status"] = "Reference library"
    enriched.loc[from_ncbi, "Taxonomy_Status"] = "NCBI"

    output_csv = layout.identification_dir(config.run_dir) / layout.FINAL_TAXONOMY_TABLE
    enriched.to_csv(output_csv, index=False)

    total = len(enriched)
    from_library = int((enriched["Taxonomy_Status"] == "Reference library").sum())
    looked_up = int(from_ncbi.sum())
    resolved = from_library + looked_up

    if resolved == 0:
        message = (
            "No taxonomy could be resolved"
            + (", because NCBI could not be reached." if network_failed else ".")
            + " The species table from the previous stage is still complete and usable."
        )
        reporter.warning(message)
        return {
            "status": "partial",
            "message": message,
            "taxonomy_csv": output_csv,
            "resolved": 0,
            "total": total,
        }

    # Said as two numbers rather than one, because a record whose lineage came
    # from the reference library is not a lesser result than one looked up -
    # it is the better of the two, curated for this marker. Reporting only the
    # lookups made a successful run look like a mostly failed one.
    parts = []
    if from_library:
        parts.append(f"{from_library} from the reference library")
    if looked_up:
        parts.append(f"{looked_up} looked up at NCBI")
    reporter.success(
        f"{resolved} of {total} records have a full lineage"
        + (" (" + ", ".join(parts) + ")." if parts else ".")
    )
    if resolved < total:
        reporter.info(
            f"{total - resolved} record(s) have no lineage from either source. "
            "They are still in the table with whatever the search could agree on."
        )

    return {
        "status": "ok" if resolved == total and not network_failed else "partial",
        "message": f"Resolved taxonomy for {resolved} of {total} records.",
        "taxonomy_csv": output_csv,
        "resolved": resolved,
        "total": total,
    }

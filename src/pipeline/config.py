# src/pipeline/config.py
import json
import os
import platform as _platform
import sys
import yaml
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

# Use src.-prefixed absolute import
from src.utils.platform import get_bundled_bin
from src.version import __version__

@dataclass
class PipelineConfig:
    """
    Central configuration object for the TaxaTag pipeline.
    Can be loaded from a YAML file or created programmatically (e.g., by the GUI).
    """

    # ========== USER INPUTS ==========
    input_dir: Path = Path(".")                 # where FASTQ/SRA live
    output_base: Path = Path("./output")        # root for run folders
    
    # SRA conversion toggle
    convert_sra: bool = False                   # User checkbox in GUI
    standardised_fastq_dir: Optional[Path] = None  # Set by Stage 0

    # ========== LOCUS & PRIMER DEFINITIONS ==========
    # Now a list of dicts, each containing:
    #   name, min_len, max_len, q_score, error_rate, abundance_filter, primers
    loci: List[Dict[str, Any]] = field(default_factory=list)

    # ========== THRESHOLDS (user adjustable) ==========
    min_identity: Dict[str, float] = field(default_factory=lambda: {
        "species": 99.0,
        "genus": 97.0,
        "family": 95.0
    })
    min_zotu_size: int = 8

    # ========== EXTERNAL TOOL PATHS (can be overridden) ==========
    # These default to the bundled binary names; the get_bundled_bin() helper
    # will locate them at runtime, but users can override them here if needed.
    vsearch_path: Path = Path("vsearch")
    blastn_path: Path = Path("blastn")
    fastqdump_path: Path = Path("fasterq-dump")

    # ========== BLAST DATABASE ==========
    blast_db: Optional[Path] = None             # user selects local .ndb

    # ========== LOCAL REFERENCE LIBRARY ==========
    # Folder holding taxatag_reference_core.db and blast_volumes/. Used when
    # blast_mode is "reference": each marker is searched against its own
    # volume, and species names come from the catalogue rather than NCBI.
    reference_dir: Optional[Path] = None

    # A named subset of that library to search instead of the whole of it -
    # see src/reference/scopes.py and docs/decisions/0006. Empty means the
    # whole volume, which is what almost every run wants.
    #
    # Deliberately absent from the window. A scope changes which references
    # can be matched at all, so a wrong one produces a shorter species list
    # with nothing to say why, and that is not a choice to put two clicks away
    # from someone who has not read what the scopes are. It is set in the
    # settings file, by someone who went looking for it.
    reference_scope: str = ""

    # ========== BLAST BEHAVIOUR ==========
    # "reference" uses TaxaTag's own library at reference_dir, searching each
    # marker against its own volume; "local" uses one BLAST database at
    # blast_db; "remote" queries NCBI over the internet, which needs no setup
    # but is slow and rate-limited.
    blast_mode: str = "remote"
    # Deliberately not 1. BLAST applies this limit while searching rather than
    # after ranking, so asking for a single hit can return one that is not the
    # best available - and sometimes none at all. Several are requested and the
    # strongest is chosen afterwards.
    blast_max_target_seqs: int = 10
    blast_evalue: float = 1e-5

    # How many sequences go to NCBI in one submission. Their public interface
    # is built for interactive-scale queries, and a single job of hundreds
    # gives no progress, no partial results, and nothing to retry when it
    # stalls. Ignored when searching locally, where one search is fastest.
    blast_chunk_size: int = 100

    # How long to wait for one of those submissions before giving up on it.
    #
    # This is an estimate, not a measurement: a healthy NCBI queue returns a
    # hundred sequences in roughly twenty minutes, and fifteen per cent is
    # added for a busy one. It is scaled by the actual chunk size. Raise it if
    # your searches are being abandoned while NCBI is merely slow.
    blast_timeout_minutes: float = 23.0

    # The smallest share of a sequence that must take part in the alignment
    # for a match to count. Identity alone is not enough: BLAST reports a
    # fragment matching 55 bases of a 256-base sequence as 100% identical,
    # which without this becomes a confident but false species call.
    min_query_coverage: float = 90.0

    # How much of the equally-good references must agree before a name is
    # given. A short marker sequence often matches many references at exactly
    # the same score, and they do not always agree; below this level of
    # agreement the sequence is reported at a coarser rank, or not at all,
    # rather than being given whichever name happened to come first.
    consensus_threshold: float = 0.9

    # ========== READ HANDLING ==========
    # The original UoS pipeline carried only R1 forward into dereplication.
    # Enabling this merges R1/R2 with VSEARCH first, which is usually the
    # better choice for amplicons longer than a single read.
    merge_reads: bool = False
    min_merge_overlap: int = 10

    # Discard ZOTUs whose length falls outside the locus min_len/max_len
    # window. Off by default because those values usually quote the amplicon
    # including primers, which is longer than what survives trimming. The
    # denoising report shows the lengths actually observed, so this can be
    # switched on once the window is known to match the data.
    apply_length_filter: bool = False

    # ========== NCBI / EXTERNAL API ==========
    ncbi_email: str = ""                        # REQUIRED for NCBI calls
    ncbi_tool: str = "taxatag_pipeline"

    # ========== RUNTIME BEHAVIOUR ==========
    threads: int = 0                            # 0 = auto-detect
    clean_output: bool = False                  # UI "Clear" button hook

    # ========== CACHING ==========
    taxonomy_cache: Path = Path("cache/taxonomy_cache.json")

    # ========== PROJECT METADATA ==========
    project_name: str = "TaxaTag"
    #: An optional name for this run, appended to the timestamp so the
    #: folder says what it holds. Empty means a bare timestamp, which is
    #: what every run before this one is called.
    run_name: str = ""

    # ========== GENERATED AT RUNTIME ==========
    run_dir: Optional[Path] = None
    logs_dir: Optional[Path] = None

    # ----------------------------------------------------------------------
    # POST-INITIALIZATION HOOK
    # ----------------------------------------------------------------------
    def __post_init__(self) -> None:
        """
        Runs automatically after initialization. Resolves generic tool flags 
        to their actual system-specific bundled paths unless overridden.
        """
        if str(self.vsearch_path) == "vsearch":
            self.vsearch_path = get_bundled_bin("vsearch")
            
        if str(self.blastn_path) == "blastn":
            self.blastn_path = get_bundled_bin("blastn")
            
        if str(self.fastqdump_path) == "fasterq-dump":
            self.fastqdump_path = get_bundled_bin("fasterq-dump")

    # ----------------------------------------------------------------------
    # CLASS METHODS
    # ----------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "PipelineConfig":
        """Load configuration from a YAML file."""
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data or {})

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineConfig":
        """
        Build a configuration from settings already read into memory.

        Split out from `from_yaml` so the settings form can apply what someone
        has just edited without writing it to a file and reading it back.
        """
        data = data or {}
        config = cls(
            project_name=data.get("project", {}).get("name", "TaxaTag"),
            run_name=data.get("project", {}).get("run_name", ""),
            input_dir=Path(data.get("paths", {}).get("input_dir", ".")),
            output_base=Path(data.get("paths", {}).get("output_base", "./output")),
            blast_db=Path(data.get("paths", {}).get("blast_db", "")) if data.get("paths", {}).get("blast_db") else None,
            reference_dir=Path(data["paths"]["reference_dir"]) if data.get("paths", {}).get("reference_dir") else None,
            reference_scope=str(data.get("paths", {}).get("reference_scope", "") or ""),
            taxonomy_cache=Path(data.get("paths", {}).get("taxonomy_cache", "cache/taxonomy_cache.json")),
            threads=data.get("runtime", {}).get("threads", 0),
            convert_sra=data.get("runtime", {}).get("convert_sra", True),
            clean_output=data.get("runtime", {}).get("clean_output", False),
            ncbi_email=data.get("resources", {}).get("ncbi_email", ""),
            ncbi_tool=data.get("resources", {}).get("ncbi_tool", "taxatag_pipeline"),
            min_identity=data.get("thresholds", {}).get("min_identity", {
                "species": 99.0, "genus": 97.0, "family": 95.0
            }),
            min_zotu_size=data.get("thresholds", {}).get("min_zotu_size", 8),
            blast_mode=data.get("runtime", {}).get("blast_mode", "remote"),
            blast_chunk_size=int(data.get("runtime", {}).get("blast_chunk_size", 100)),
            blast_timeout_minutes=float(
                data.get("runtime", {}).get("blast_timeout_minutes", 23.0)
            ),
            min_query_coverage=float(
                data.get("thresholds", {}).get("min_query_coverage", 90.0)
            ),
            consensus_threshold=float(
                data.get("thresholds", {}).get("consensus_threshold", 0.9)
            ),
            blast_max_target_seqs=data.get("runtime", {}).get("blast_max_target_seqs", 10),
            blast_evalue=float(data.get("runtime", {}).get("blast_evalue", 1e-5)),
            merge_reads=data.get("runtime", {}).get("merge_reads", False),
            min_merge_overlap=data.get("runtime", {}).get("min_merge_overlap", 10),
            apply_length_filter=data.get("runtime", {}).get("apply_length_filter", False),
            loci=data.get("loci", [])
        )

        # Preserve tool overrides if they exist in the YAML (optional)
        if "paths" in data and "vsearch" in data["paths"]:
            config.vsearch_path = Path(data["paths"]["vsearch"])
        if "paths" in data and "blastn" in data["paths"]:
            config.blastn_path = Path(data["paths"]["blastn"])
        if "paths" in data and "fastqdump" in data["paths"]:
            config.fastqdump_path = Path(data["paths"]["fastqdump"])

        return config

    @staticmethod
    def _folder_safe(name: str) -> str:
        """
        A user's run name, reduced to something a filesystem will accept.

        Spaces become hyphens and anything a path cannot carry is dropped,
        rather than the name being refused. Somebody typing "Pond A / June
        (repeat)" has said what they mean, and answering with a validation
        error teaches them only that the box is fussy.

        Trimmed to a length that leaves room underneath. Windows cannot open
        a path over 260 characters, and a run folder is the *shortest* part
        of what goes below it - `05_results/<table>` and
        `02_trimmed/<locus>/<sample>_trimmed.fastq.gz` are both longer.
        """
        import re

        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name).strip())
        return cleaned.strip("-.")[:40]

    def create_run_directory(self) -> Path:
        """
        Generate a dated run folder inside output_base/runs/.

        The timestamp comes first and a name, if given, is appended:
        `2026-09-10_094341_pond-survey`. That order is the whole design.
        Replacing the timestamp would break every listing that reads these
        folders in order; following it costs nothing and makes the folder
        say what it holds, which is what somebody scrolling a results
        directory in a file manager actually needs.
        """
        if self.run_dir is None:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            named = self._folder_safe(self.run_name)
            folder = f"{timestamp}_{named}" if named else timestamp
            run_root = self.output_base / "runs"
            run_root.mkdir(parents=True, exist_ok=True)
            self.run_dir = run_root / folder
            self.run_dir.mkdir(parents=True, exist_ok=True)

            self.logs_dir = self.run_dir / "logs"
            self.logs_dir.mkdir(exist_ok=True)
        return self.run_dir

    def resolve_threads(self) -> int:
        """Return a concrete thread count (config value, or all cores when 0)."""
        if self.threads and self.threads > 0:
            return int(self.threads)
        return max(1, os.cpu_count() or 1)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise back to the same shape as the YAML file."""
        return {
            "project": {
                "name": self.project_name,
                "run_name": self.run_name,
                "version": __version__,
            },
            "paths": {
                "input_dir": str(self.input_dir),
                "output_base": str(self.output_base),
                "blast_db": str(self.blast_db) if self.blast_db else "",
                "reference_dir": str(self.reference_dir) if self.reference_dir else "",
                "reference_scope": self.reference_scope,
                "taxonomy_cache": str(self.taxonomy_cache),
            },
            "runtime": {
                "threads": self.threads,
                "convert_sra": self.convert_sra,
                "clean_output": self.clean_output,
                "blast_mode": self.blast_mode,
                "blast_max_target_seqs": self.blast_max_target_seqs,
                "blast_evalue": self.blast_evalue,
                "blast_chunk_size": self.blast_chunk_size,
                "blast_timeout_minutes": self.blast_timeout_minutes,
                "merge_reads": self.merge_reads,
                "min_merge_overlap": self.min_merge_overlap,
                "apply_length_filter": self.apply_length_filter,
            },
            "resources": {"ncbi_email": self.ncbi_email, "ncbi_tool": self.ncbi_tool},
            "thresholds": {
                "min_identity": self.min_identity,
                "min_zotu_size": self.min_zotu_size,
                "min_query_coverage": self.min_query_coverage,
                "consensus_threshold": self.consensus_threshold,
            },
            "loci": self.loci,
        }

    def to_yaml(self, yaml_path: Path) -> Path:
        """Write this configuration out as a YAML file."""
        yaml_path = Path(yaml_path)
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.to_dict(), f, sort_keys=False, allow_unicode=True)
        return yaml_path

    def write_manifest(self) -> Optional[Path]:
        """
        Record exactly what was run, where, and with which tools.

        Written into the run folder so a result can always be traced back to
        the settings and binaries that produced it.
        """
        if self.run_dir is None:
            return None
        manifest = {
            "project_name": self.project_name,
            "started": datetime.now().isoformat(timespec="seconds"),
            "run_dir": str(self.run_dir),
            "platform": {
                "system": _platform.system(),
                "release": _platform.release(),
                "machine": _platform.machine(),
                "python": sys.version.split()[0],
                "frozen": bool(getattr(sys, "frozen", False)),
            },
            "tools": {
                "vsearch": str(self.vsearch_path),
                "blastn": str(self.blastn_path),
                "fasterq-dump": str(self.fastqdump_path),
            },
            "settings": self.to_dict(),
        }
        path = self.run_dir / "run_manifest.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, default=str)
        # A copy of the settings in the same format the user edits them.
        self.to_yaml(self.run_dir / "config_used.yaml")
        return path

    def find_latest_run(self) -> Optional[Path]:
        """
        The most recent run folder under output_base, if there is one.

        Ordered by what each run recorded about itself rather than by folder
        name, so a renamed folder is still placed correctly - see
        `layout.runs_by_recency`.
        """
        from src.pipeline import layout

        runs = layout.runs_by_recency(self.output_base)
        return runs[0] if runs else None

    def adopt_run_directory(self, run_dir: Path) -> Path:
        """Continue working inside an existing run folder rather than a new one."""
        self.run_dir = Path(run_dir)
        # Imported here rather than at the top: layout has no dependencies of
        # its own, but config is imported by almost everything, and a plain
        # top-level import between the two would make that circular.
        from src.pipeline import layout as layout_module

        self.logs_dir = self.run_dir / layout_module.LOGS
        standardised = self.run_dir / layout_module.STANDARDISED
        if standardised.exists():
            self.standardised_fastq_dir = standardised
        return self.run_dir

    def active_loci(self) -> List[Dict]:
        """
        The primer sets this run should actually look for.

        A set can be switched off without being deleted, because the
        alternative is retyping four primer sequences every time a project
        alternates between markers - and a retyped primer is a mistyped
        primer. A set with no `enabled` key predates the switch and is on,
        so an older settings file behaves as it always did.
        """
        return [locus for locus in self.loci if locus.get("enabled", True)]

    def get_locus_by_name(self, name: str) -> Optional[Dict]:
        """
        Retrieve a locus definition by its name.

        Searches every locus, not only the enabled ones: this is asked by the
        stages that read back what a previous stage wrote, and a set switched
        off after trimming still has to be describable.
        """
        for locus in self.loci:
            if locus.get("name") == name:
                return locus
        return None

    def get_abundance_filter(self, locus_name: str) -> float:
        """
        Get abundance filter for a specific locus.
        Falls back to a sensible default (0.0002) if not set per-locus.
        """
        locus = self.get_locus_by_name(locus_name)
        if locus and "abundance_filter" in locus:
            return locus["abundance_filter"]
        return 0.0002

    def get_primers(self, locus_name: str) -> Optional[Dict]:
        """Get the primer dictionary for a given locus."""
        locus = self.get_locus_by_name(locus_name)
        if locus and "primers" in locus:
            return locus["primers"]
        return None

    # For backward compatibility with old scripts that expect a top-level 'primers' dict
    # This generates a combined dict: {locus_name: primer_dict}
    @property
    def primers(self) -> Dict[str, Dict]:
        """Backward-compatible primer dictionary."""
        result = {}
        for locus in self.loci:
            name = locus.get("name")
            primers = locus.get("primers")
            if name and primers:
                result[name] = primers
        return result

    # For backward compatibility with old scripts that expect a list of loci names
    @property
    def locus_names(self) -> List[str]:
        """Backward-compatible list of locus names."""
        return [l.get("name") for l in self.loci if l.get("name")]
# src/pipeline/resume.py
"""
Picking up a run that stopped part-way.

An analysis takes minutes to hours, and the things that interrupt one - a
stopped run, a wrong database path, a closed laptop lid - almost never make
the work already done wrong. The trimmed reads and denoised sequences sitting
in the run folder are still perfectly good. Resuming means working out how far
a run got and starting again at the next stage, instead of spending another
two hours arriving back at files that were already there.

How far a run got is written to `run_state.json` as each stage finishes,
rather than when the run ends, because a run that reached its end is not one
anybody needs to resume. Where that file is missing - a run made before this
existed, or a folder copied by hand - the folder's own contents are read
instead. That is a guess rather than a record, and it says so, because a stage
killed halfway through leaves a folder that looks just like a finished one.

Nothing here imports a stage or the interface, so the window can ask what is
resumable without loading the pipeline, and the pipeline can record its
progress without knowing whether anyone is watching.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

import yaml

from src.pipeline import layout

if TYPE_CHECKING:  # imported for type checking only, to stay import-light
    from src.pipeline.config import PipelineConfig


#: The settings each stage reads, by the name they carry in the settings file.
#:
#: This is what makes resuming safe to offer. If one of these changed since the
#: run was made, the output that stage already produced no longer matches the
#: settings now in front of the user, and carrying on past it would quietly mix
#: two different analyses into one results table. Settings absent from a stage's
#: list can be changed freely - which is the point, because the usual reason to
#: resume is that something like a database path was wrong and has been fixed.
#:
#: Settings that change how long a stage takes but not what it produces, such as
#: the thread count, are deliberately left out.
STAGE_INPUTS: Dict[str, Tuple[str, ...]] = {
    "standardise": ("input_dir", "convert_sra"),
    "trim": ("loci",),
    # Stage 2 is handed the locus definition but does not use it: _merge_pairs
    # deliberately applies no length window, because the quoted amplicon length
    # is not the length that survives trimming. Editing a primer set therefore
    # cannot change what this stage wrote.
    "dereplicate": ("merge_reads", "min_merge_overlap"),
    # Stage 3 reads min_len and max_len only to note in its report whether the
    # observed median fell inside them. Nothing is filtered on it, so a changed
    # window makes the note stale but the ZOTUs still correct - not worth
    # spending a warning on.
    "denoise": ("min_zotu_size",),
    "blast": (
        "blast_mode", "blast_db", "reference_dir", "blast_evalue",
        "blast_max_target_seqs", "min_identity", "min_query_coverage",
        "consensus_threshold", "apply_length_filter", "loci",
    ),
    # Stage 5 attaches names to identifiers already decided in stage 4. The
    # email address is a courtesy header for NCBI, not an input to the answer.
    "taxonomy": (),
}

#: Settings named the way they are labelled in the window, so a warning reads
#: as a sentence rather than as a variable name.
SETTING_NAMES: Dict[str, str] = {
    "input_dir": "the folder holding your sequencing files",
    "convert_sra": "whether SRA files are converted",
    "loci": "the primer sets",
    "merge_reads": "joining forward and reverse reads",
    "min_merge_overlap": "the minimum overlap when joining reads",
    "min_zotu_size": "the minimum sequence abundance",
    "blast_mode": "where to search",
    "blast_db": "the database folder",
    "reference_dir": "the reference library folder",
    "blast_evalue": "the search e-value",
    "blast_max_target_seqs": "how many matches to consider",
    "min_identity": "the identity thresholds",
    "min_query_coverage": "how much of a sequence a match must cover",
    "consensus_threshold": "the agreement between references",
    "apply_length_filter": "the sequence length filter",
}


@dataclass
class ResumePoint:
    """What is known about an unfinished run, and what resuming it would do."""

    run_dir: Path
    completed: List[str]
    remaining: List[str]
    #: True when the stage list was read from the folder rather than recorded.
    inferred: bool = False
    #: Settings the finished stages depended on that have since changed.
    stale_settings: List[str] = field(default_factory=list)
    #: The earliest finished stage whose settings changed, if any. Its output
    #: no longer matches the settings in front of the user, so it and
    #: everything after it have been moved out of `completed` and back into
    #: `remaining` - resuming runs them again rather than trusting them.
    redo_from: Optional[str] = None
    #: How many stages that pushed back.
    redone: List[str] = field(default_factory=list)
    #: When the last stage finished, as text, or "" if not recorded.
    finished_at: str = ""

    @property
    def can_resume(self) -> bool:
        """True when there is both something to keep and something left to do."""
        return bool(self.completed) and bool(self.remaining)

    @property
    def next_stage(self) -> Optional[str]:
        return self.remaining[0] if self.remaining else None

    @property
    def next_title(self) -> str:
        stage = self.next_stage
        return layout.STAGE_TITLES.get(stage, "") if stage else ""

    @property
    def started(self) -> str:
        """The run folder's timestamp, rendered the way a person writes one."""
        try:
            stamp = datetime.strptime(self.run_dir.name, "%Y-%m-%d_%H%M%S")
        except (ValueError, AttributeError):
            return self.run_dir.name
        return stamp.strftime("%d %B %Y at %H:%M")

    def summary(self) -> str:
        """A description of what resuming would do, for a confirmation dialog."""
        kept = "\n".join(
            f"    {layout.STAGE_TITLES.get(s, s)}" for s in self.completed
        )
        todo = "\n".join(
            f"    {layout.STAGE_TITLES.get(s, s)}" for s in self.remaining
        )
        parts = [
            f"The run started on {self.started} stopped before it finished.",
            "",
            "Already done, and will be kept:",
            kept,
            "",
            "Still to do:",
            todo,
        ]
        if self.inferred:
            parts += [
                "",
                "TaxaTag worked this out by looking at what is in the run "
                "folder, because the run was made before it kept a record of "
                "its own progress. If a stage was interrupted part-way its "
                "folder can look finished, so check the results afterwards.",
            ]
        if self.redo_from:
            changed = ", ".join(SETTING_NAMES.get(s, s) for s in self.stale_settings)
            redone = ", ".join(
                layout.STAGE_TITLES.get(s, s).split(" - ")[-1].lower()
                for s in self.redone
            )
            parts += [
                "",
                f"You changed {changed} since that run. What it already "
                f"produced was made with the old value, so {redone} will be "
                "done again rather than kept - the results would otherwise be "
                "a mixture of two different analyses.",
            ]
        return "\n".join(parts)


# ----------------------------------------------------------------------
# Recording progress
# ----------------------------------------------------------------------
def record_stage(run_dir: Optional[Path], stage: str) -> None:
    """Note that a stage finished, so a later run can start after it."""
    if run_dir is None:
        return
    completed = _read_record(run_dir) or []
    if stage not in completed:
        completed.append(stage)
    payload = {
        # Held in pipeline order, so re-running one stage on its own does not
        # leave the record claiming the run happened out of sequence.
        "completed": _in_order(completed),
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        path = layout.run_state_path(run_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        # A bookmark that cannot be written is a lost convenience, not a lost
        # run. Resume falls back to reading the folder.
        pass


# ----------------------------------------------------------------------
# Reading it back
# ----------------------------------------------------------------------
def inspect(run_dir: Path, config: Optional["PipelineConfig"] = None) -> ResumePoint:
    """Work out how far one run got, and what resuming it would involve."""
    run_dir = Path(run_dir)
    recorded = _read_record(run_dir)
    if recorded is not None:
        completed, inferred = _in_order(recorded), False
    else:
        completed, inferred = _infer_completed(run_dir), True

    # Only an unbroken run of stages from the start can be skipped: stage 3's
    # output is no use if stage 2's is missing, however complete it looks.
    completed = _leading_run(completed)

    # A stage whose settings changed did not produce what the settings now in
    # front of the user would produce. Rather than warn and let the user carry
    # on into a results table that mixes two analyses, the run is pushed back
    # to before that stage and it is done again. If the very first stage is
    # affected there is nothing left to keep, `can_resume` goes false, and the
    # window stops offering to resume at all - which is the right answer,
    # because at that point resuming and starting fresh are the same thing.
    redo_from, changed = first_invalidated(run_dir, config, completed)
    redone: List[str] = []
    if redo_from:
        cut = completed.index(redo_from)
        redone, completed = completed[cut:], completed[:cut]

    remaining = [s for s in layout.STAGE_ORDER if s not in completed]

    return ResumePoint(
        run_dir=run_dir,
        completed=completed,
        remaining=remaining,
        inferred=inferred,
        stale_settings=changed,
        redo_from=redo_from,
        redone=redone,
        finished_at=_read_timestamp(run_dir),
    )


def find_resumable(config: "PipelineConfig") -> Optional[ResumePoint]:
    """
    The most recent run, if it stopped part-way and could be continued.

    Only the most recent one is offered. Looking further back would mean
    asking a user to choose between run folders by timestamp, which is exactly
    the kind of decision this program exists to avoid.
    """
    latest = config.find_latest_run()
    if latest is None:
        return None
    point = inspect(latest, config)
    return point if point.can_resume else None


# ----------------------------------------------------------------------
# Settings that the completed stages depended on
# ----------------------------------------------------------------------
def stale_settings(
    run_dir: Path,
    config: Optional["PipelineConfig"],
    completed: Sequence[str],
) -> List[str]:
    """Which settings the finished stages relied on have changed since."""
    if config is None:
        return []
    saved = _saved_settings(run_dir)
    if not saved:
        return []
    current = _flatten(config.to_dict())
    watched = {name for stage in completed for name in STAGE_INPUTS.get(stage, ())}
    return sorted(
        name for name in watched
        if name in saved and saved[name] != current.get(name)
    )


def first_invalidated(
    run_dir: Path,
    config: Optional["PipelineConfig"],
    completed: Sequence[str],
) -> Tuple[Optional[str], List[str]]:
    """
    The earliest finished stage whose settings changed, and which changed.

    Stages are checked in the order they ran, because that is the order the
    damage propagates in: if trimming used different primers, everything
    downstream of it was built from different reads, whether or not its own
    settings also changed.

    Returns (None, []) when everything finished still matches.
    """
    if config is None:
        return None, []
    saved = _saved_settings(run_dir)
    if not saved:
        return None, []
    current = _flatten(config.to_dict())

    for stage in completed:
        changed = sorted(
            name for name in STAGE_INPUTS.get(stage, ())
            if name in saved and saved[name] != current.get(name)
        )
        if changed:
            return stage, changed
    return None, []


def _saved_settings(run_dir: Path) -> Dict:
    """The settings the run was made with, from its own copy in the folder."""
    path = Path(run_dir) / "config_used.yaml"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return _flatten(yaml.safe_load(handle) or {})
    except (OSError, yaml.YAMLError):
        return {}


def _flatten(data: Dict) -> Dict:
    """
    Collapse the settings file's sections into one lookup.

    The file groups settings under `paths`, `runtime`, `thresholds` and so on
    for a reader's benefit, but every name in it is unique, so a stage's inputs
    can be listed by name alone rather than by name and section.
    """
    flat: Dict = {}
    for key, value in data.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------
def _read_record(run_dir: Path) -> Optional[List[str]]:
    """The recorded stage list, or None when there is no record to read."""
    try:
        with open(layout.run_state_path(run_dir), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    completed = data.get("completed")
    return list(completed) if isinstance(completed, list) else None


def _read_timestamp(run_dir: Path) -> str:
    try:
        with open(layout.run_state_path(run_dir), "r", encoding="utf-8") as handle:
            return str(json.load(handle).get("updated", ""))
    except (OSError, json.JSONDecodeError):
        return ""


def _in_order(stages: Sequence[str]) -> List[str]:
    """The given stages, in the order the pipeline runs them."""
    return [stage for stage in layout.STAGE_ORDER if stage in stages]


def _leading_run(stages: Sequence[str]) -> List[str]:
    """The stages done from the beginning without a gap."""
    done: List[str] = []
    for stage in layout.STAGE_ORDER:
        if stage not in stages:
            break
        done.append(stage)
    return done


def _holds(directory: Path, pattern: str) -> bool:
    """True when a folder contains at least one matching file, at any depth."""
    directory = Path(directory)
    if not directory.is_dir():
        return False
    return any(directory.rglob(pattern))


def _infer_completed(run_dir: Path) -> List[str]:
    """
    Guess how far a run got from what is in its folder.

    Used only for runs made before TaxaTag kept a record. Each stage is judged
    by whether the folder it fills has anything in it, which cannot distinguish
    a finished stage from one stopped halfway, so callers say so.
    """
    identification = layout.identification_dir(run_dir)
    evidence = {
        "standardise": _holds(layout.standardised_dir(run_dir), "*.fastq*"),
        "trim": _holds(layout.trimmed_dir(run_dir), "*.fastq*"),
        "dereplicate": _holds(layout.dereplicated_dir(run_dir), "*.fast*"),
        "denoise": _holds(layout.denoised_dir(run_dir), "*.fast*"),
        "blast": (identification / layout.FINAL_SPECIES_TABLE).exists(),
        "taxonomy": (identification / layout.FINAL_TAXONOMY_TABLE).exists(),
    }
    return _leading_run([s for s in layout.STAGE_ORDER if evidence.get(s)])

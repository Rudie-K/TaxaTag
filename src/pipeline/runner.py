# src/pipeline/runner.py
"""
Runs the six pipeline stages in order.

Both the graphical interface and the command line go through here, so a run
started from a window and a run started from a terminal do exactly the same
thing and produce exactly the same folder.
"""

from __future__ import annotations

import json
import shutil
import time
import traceback
from pathlib import Path
from typing import Callable, Dict, List, Optional

from src.pipeline import layout, resume
from src.pipeline.config import PipelineConfig
from src.pipeline.stage0_standardise import run_stage0
from src.pipeline.stage1_trim import run_stage1
from src.pipeline.stage2_dereplicate import run_stage2
from src.pipeline.stage3_denoise import run_stage3
from src.pipeline.stage4_blast import run_stage4
from src.pipeline.stage5_taxonomy import run_stage5
from src.utils.reporting import PipelineCancelled, Reporter, console_reporter

StageFunction = Callable[[PipelineConfig, Reporter], dict]

STAGE_FUNCTIONS: Dict[str, StageFunction] = {
    "standardise": run_stage0,
    "trim": run_stage1,
    "dereplicate": run_stage2,
    "denoise": run_stage3,
    "blast": run_stage4,
    "taxonomy": run_stage5,
}


class StageReporter(Reporter):
    """
    Wraps a Reporter so a stage's own 0-100% progress becomes a slice of the
    whole run's progress bar. Without this the bar would restart six times.
    """

    def __init__(self, parent: Reporter, start: float, end: float):
        super().__init__(
            log_fn=parent._log_fn,
            progress_fn=parent._progress_fn,
            cancel_check=parent._cancel_check,
            verbose=parent.verbose,
        )
        self._start = start
        self._end = end

    def progress(self, fraction: Optional[float], message: str = "") -> None:
        if fraction is None:
            super().progress(None, message)
            return
        span = self._end - self._start
        super().progress(self._start + span * max(0.0, min(1.0, fraction)), message)


def _prepare_output(config: PipelineConfig, reporter: Reporter) -> None:
    """Clear previous results if the user asked for a clean start."""
    if not config.clean_output:
        return
    runs_root = config.output_base / "runs"
    if runs_root.exists():
        reporter.info(f"Clearing previous runs from {runs_root}")
        shutil.rmtree(runs_root, ignore_errors=True)


def run_pipeline(
    config: PipelineConfig,
    reporter: Optional[Reporter] = None,
    stages: Optional[List[str]] = None,
    run_dir: Optional[Path] = None,
) -> dict:
    """
    Run the pipeline from start to finish, or a chosen part of it.

    Returns a summary describing what happened, including per-stage results,
    so a caller can report the outcome without having watched the log.

    A run that does not begin at the first stage continues inside an existing
    run folder, since its input is the previous stage's output. That folder is
    `run_dir` when given, otherwise the most recent one. `src/pipeline/resume.py`
    works out which stages a folder still needs.
    """
    reporter = reporter or console_reporter()
    stages = stages or list(layout.STAGE_ORDER)
    unknown = [s for s in stages if s not in STAGE_FUNCTIONS]
    if unknown:
        raise ValueError(f"Unknown stage(s): {', '.join(unknown)}")

    started = time.monotonic()
    results: Dict[str, dict] = {}
    resuming = stages[0] != layout.STAGE_ORDER[0]

    try:
        if run_dir is not None:
            if not Path(run_dir).exists():
                return _failure(
                    f"There is no run folder at {run_dir}.", config, started
                )
            config.adopt_run_directory(Path(run_dir))
            reporter.info(f"Continuing the run in {config.run_dir}")
        elif resuming:
            previous = config.find_latest_run()
            if previous is None:
                return _failure(
                    f"Starting at '{stages[0]}' needs the earlier stages' output, "
                    f"but there is no previous run in {config.output_base / 'runs'}. "
                    "Run the whole pipeline first.",
                    config, started,
                )
            config.adopt_run_directory(previous)
            reporter.info(f"Continuing the most recent run in {config.run_dir}")
        else:
            _prepare_output(config, reporter)
            config.create_run_directory()

        config.write_manifest()
        layout.ensure(layout.reports_dir(config.run_dir), layout.logs_dir(config.run_dir))

        reporter.info(f"Results will be written to {config.run_dir}")

        for index, stage_name in enumerate(stages):
            reporter.checkpoint()
            slice_start = index / len(stages)
            slice_end = (index + 1) / len(stages)
            stage_reporter = StageReporter(reporter, slice_start, slice_end)

            # Timed per stage, not just per run.
            #
            # Without this the only number anyone has is how long the whole
            # thing took, which answers no question worth asking. A user
            # deciding whether to wait, and anyone deciding what to make
            # faster, both need to know which stage the hours went into - and
            # the answer changes completely between a run searching NCBI and
            # one searching a local library.
            stage_started = time.monotonic()
            result = STAGE_FUNCTIONS[stage_name](config, stage_reporter)
            result["seconds"] = time.monotonic() - stage_started
            results[stage_name] = result
            reporter.info(
                f"{layout.STAGE_TITLES[stage_name].split(' - ')[0]} took "
                f"{_format_duration(result['seconds'])}."
            )

            if result.get("status") == "error":
                reporter.error(result.get("message", "The stage failed."))
                return {
                    "status": "error",
                    "message": result.get("message", "The stage failed."),
                    "failed_stage": stage_name,
                    "stage_title": layout.STAGE_TITLES[stage_name],
                    "results": results,
                    "run_dir": config.run_dir,
                    "seconds": time.monotonic() - started,
                }

            # Recorded here rather than at the end of the run: a run that
            # reaches the end is not one anybody needs to resume, and a run
            # killed outright never gets to write anything afterwards.
            resume.record_stage(config.run_dir, stage_name)

        reporter.progress(1.0, "Finished")

    except PipelineCancelled:
        reporter.warning("Run stopped. Everything completed so far is in the run folder.")
        return {
            "status": "cancelled",
            "message": "The run was stopped before it finished.",
            "results": results,
            "run_dir": config.run_dir,
            "seconds": time.monotonic() - started,
        }
    except Exception as error:  # noqa: BLE001 - a crash must still be reportable
        reporter.error(f"Unexpected problem: {error}")
        reporter.debug(traceback.format_exc())
        return {
            "status": "error",
            "message": f"The run stopped because of an unexpected problem: {error}",
            "traceback": traceback.format_exc(),
            "results": results,
            "run_dir": config.run_dir,
            "seconds": time.monotonic() - started,
        }

    elapsed = time.monotonic() - started
    record_timings(config.run_dir, results, elapsed, config)
    partial = any(r.get("status") == "partial" for r in results.values())

    species_csv = layout.identification_dir(config.run_dir) / layout.FINAL_SPECIES_TABLE
    taxonomy_csv = layout.identification_dir(config.run_dir) / layout.FINAL_TAXONOMY_TABLE
    final_table = taxonomy_csv if taxonomy_csv.exists() else species_csv

    reporter.success(f"Finished in {_format_duration(elapsed)}.")
    if final_table.exists():
        reporter.info(f"Results table: {final_table}")

    return {
        "status": "partial" if partial else "ok",
        "message": f"Finished in {_format_duration(elapsed)}.",
        "results": results,
        "run_dir": config.run_dir,
        "final_table": final_table if final_table.exists() else None,
        "seconds": elapsed,
    }


def record_timings(
    run_dir: Optional[Path],
    results: Dict[str, dict],
    elapsed: float,
    config: Optional[PipelineConfig] = None,
) -> None:
    """
    Add how long each stage took to the run's manifest.

    The manifest is written before anything runs, because its point is to
    record what a result came from and that has to survive a crash. Durations
    only exist afterwards, so they are added rather than included.

    Kept in the manifest instead of only in the log because the log is a
    stream a user scrolls past, and this is the thing to compare between runs:
    the same samples, a different library, and the numbers say which change
    mattered.
    """
    if run_dir is None:
        return
    path = Path(run_dir) / "run_manifest.json"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        # A missing or unreadable manifest is not worth failing a finished run
        # over. The result is already on disk; this is a note about it.
        return

    # How much work this was, so a later run can be estimated from it. A
    # duration on its own says only how long *that* run took; divided by the
    # work it did, it says how long the next one will take.
    #
    # Samples times markers, because the stages that dominate - trimming and
    # dereplication - run once per sample per locus. Four samples across four
    # markers is sixteen units, and an hour spent on it predicts a quarter of
    # an hour for the same four samples on one marker.
    samples = len((results.get("standardise") or {}).get("samples") or [])
    markers = len({
        locus.get("marker", "")
        for locus in (config.active_loci() if config else [])
        if locus.get("marker")
    })

    manifest["timing"] = {
        "seconds_total": round(elapsed, 1),
        "samples": samples,
        "markers": markers,
        "seconds_by_stage": {
            name: round(result.get("seconds", 0.0), 1)
            for name, result in results.items()
        },
    }
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, default=str)
    except OSError:
        return


def _failure(message: str, config: PipelineConfig, started: float) -> dict:
    """A run that could not start, in the same shape as any other result."""
    return {
        "status": "error",
        "message": message,
        "results": {},
        "run_dir": config.run_dir,
        "seconds": time.monotonic() - started,
    }


def _format_duration(seconds: float) -> str:
    """Render a duration the way a person would say it."""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"

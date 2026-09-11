# src/pipeline/ncbi_remote.py
"""
Searching NCBI without `blastn -remote`.

`blastn -remote` speaks Blast4, an ASN.1 remote-procedure protocol, to NCBI.
On some machines and networks that submission never completes: the client sits
silent for as long as it is allowed to and then reports "Connection stream is
in bad state (Blast4-request)". Nothing about the query causes it - the same
sequence, submitted to NCBI's ordinary web interface from the same machine at
the same moment, comes back in twelve seconds.

So the submission is done here over plain HTTPS, the interface every BLAST web
user goes through, and the results are collected with `blast_formatter -rid`,
which fetches a finished search by its id and formats it with exactly the same
columns the local search produces. That keeps everything downstream unchanged
and replaces only the step that does not work.

The other thing this buys is a request id. `blastn -remote` holds its id
privately, so a search that has to be abandoned has to be submitted again from
the back of the queue - which is why a timeout and a retry, together, could
never succeed. Here the id outlives the wait: waiting longer costs nothing but
time, and a search interrupted halfway can be collected later.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.utils.reporting import Reporter

#: NCBI's public BLAST interface.
BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"

#: How long one HTTP call may take. This is not how long a search may take:
#: a search is many short calls, one to submit and the rest to ask whether it
#: has finished.
HTTP_TIMEOUT = 120

#: How often to ask whether a search has finished. NCBI ask for no more than
#: one status request per minute, and they mean it, but they also return their
#: own estimate of how long the search will take, which is what is waited
#: first.
POLL_SECONDS = 20
MINIMUM_FIRST_WAIT = 5


class RemoteSearchError(Exception):
    """NCBI refused a search, or answered with something unusable."""


@dataclass
class Submission:
    """A search that NCBI has accepted and is now working on."""

    rid: str
    #: NCBI's own estimate of the wait, in seconds. Not a promise.
    estimated_seconds: int


def _session(tool: str, email: str) -> requests.Session:
    """A session that identifies itself and backs off when asked to."""
    session = requests.Session()
    session.headers.update(
        {"User-Agent": f"{tool} ({email or 'no-email-configured'})"}
    )
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=Retry(
                total=4,
                backoff_factor=2,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET", "POST"],
            )
        ),
    )
    return session


def _field(text: str, name: str) -> Optional[str]:
    """
    Read one value out of NCBI's reply.

    Their replies are HTML pages with the machine-readable part in a comment,
    and the spacing is not consistent between them: a submission answers
    "RID = 123" while a status check answers "Status=WAITING".
    """
    match = re.search(rf"^\s*{name}\s*=\s*(.*?)\s*$", text, re.M)
    return match.group(1) if match else None


def submit(
    query_fasta: Path,
    *,
    database: str,
    hitlist_size: int,
    expect: Optional[float],
    tool: str,
    email: str,
) -> Submission:
    """Hand a FASTA file to NCBI and get back the id of the search."""
    parameters = {
        "CMD": "Put",
        "PROGRAM": "blastn",
        "MEGABLAST": "on",
        "DATABASE": database,
        "QUERY": Path(query_fasta).read_text(encoding="utf-8"),
        "HITLIST_SIZE": str(hitlist_size),
    }
    if expect:
        parameters["EXPECT"] = str(expect)

    session = _session(tool, email)
    try:
        response = session.post(BLAST_URL, data=parameters, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as error:
        raise RemoteSearchError(f"the submission could not be sent - {error}") from error

    rid = _field(response.text, "RID")
    if not rid:
        # No id means the submission itself was turned away, and NCBI say why
        # in the page they return. Their words are more use than ours.
        message = _readable_message(response.text)
        raise RemoteSearchError(f"NCBI would not accept the search - {message}")

    try:
        estimate = int(_field(response.text, "RTOE") or 0)
    except ValueError:
        estimate = 0
    return Submission(rid=rid, estimated_seconds=estimate)


def wait_for(
    submission: Submission,
    *,
    reporter: Reporter,
    timeout_seconds: Optional[float],
    tool: str,
    email: str,
) -> bool:
    """
    Wait until NCBI has finished the search.

    Returns True when there are results to collect, False when the search
    finished with nothing found. Raises if it failed or the wait ran out; the
    request id survives either way, so waiting again is always possible.
    """
    session = _session(tool, email)
    started = time.monotonic()

    # NCBI's own estimate is worth honouring: asking sooner only adds load and
    # cannot make the answer arrive earlier.
    first_wait = max(MINIMUM_FIRST_WAIT, min(submission.estimated_seconds, 60))
    _sleep(first_wait, reporter)

    while True:
        reporter.checkpoint()
        waited = time.monotonic() - started
        try:
            response = session.get(
                BLAST_URL,
                params={
                    "CMD": "Get",
                    "RID": submission.rid,
                    "FORMAT_OBJECT": "SearchInfo",
                },
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            # One failed status check is not a failed search. The id is still
            # good, so this is worth trying again.
            reporter.debug(f"    could not reach NCBI ({error}); trying again")
            _sleep(POLL_SECONDS, reporter)
            continue

        status = _field(response.text, "Status")
        if status == "READY":
            return _field(response.text, "ThereAreHits") == "yes"
        if status == "FAILED":
            raise RemoteSearchError(
                f"NCBI reported that search {submission.rid} failed. "
                f"{_readable_message(response.text)}"
            )
        if status == "UNKNOWN":
            raise RemoteSearchError(
                f"NCBI no longer recognises search {submission.rid}; it has "
                "expired and needs submitting again."
            )

        if timeout_seconds is not None and waited > timeout_seconds:
            raise RemoteSearchError(
                f"NCBI has been working on search {submission.rid} for "
                f"{int(waited / 60)} min and has not finished. The search is "
                "still theirs and is not lost."
            )

        reporter.progress(
            None, f"Waiting for NCBI ({int(waited)}s so far, search {submission.rid})"
        )
        _sleep(POLL_SECONDS, reporter)


def _sleep(seconds: float, reporter: Reporter) -> None:
    """Sleep, but keep answering the Stop button while doing it."""
    for _ in range(int(seconds)):
        reporter.checkpoint()
        time.sleep(1)


def _readable_message(page: str) -> str:
    """The human-readable part of an NCBI page, for an error message."""
    text = re.sub(r"<[^>]+>", " ", page)
    for keyword in ("Message ID", "Error", "CPU usage limit", "cannot"):
        match = re.search(rf"({keyword}[^.]{{0,200}}\.)", text)
        if match:
            return " ".join(match.group(1).split())
    return "no explanation was given"


def formatter_path(blastn_path: Path) -> Path:
    """`blast_formatter`, which lives beside `blastn` in every BLAST+ release."""
    blastn_path = Path(blastn_path)
    return blastn_path.with_name("blast_formatter" + blastn_path.suffix)


# ----------------------------------------------------------------------
# Remembering a search that has not finished yet
# ----------------------------------------------------------------------
#: Searches NCBI has accepted but not yet returned, kept in the run folder.
PENDING_FILE = "ncbi_searches.json"


def remember(folder: Path, label: str, rid: str, sequence_count: int) -> None:
    """
    Write down a search NCBI has accepted, before waiting for it.

    NCBI keep a finished search for a day or so, so an id is worth more than
    the wait that produced it: if the queue outlasts our patience, or the run
    is stopped, or the machine is shut down, the search is still theirs to
    finish and can be collected later rather than submitted again. Resuming
    the run picks these up.
    """
    records = _pending(folder)
    records[label] = {
        "rid": rid,
        "submitted": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sequences": sequence_count,
    }
    _write_pending(folder, records)


def recall(folder: Path, label: str) -> Optional[str]:
    """The id of a search submitted earlier for this batch, if there is one."""
    record = _pending(folder).get(label)
    return record.get("rid") if isinstance(record, dict) else None


def forget(folder: Path, label: str) -> None:
    """Drop a search once its results are safely collected."""
    records = _pending(folder)
    if records.pop(label, None) is not None:
        _write_pending(folder, records)


def _pending(folder: Path) -> dict:
    try:
        with open(Path(folder) / PENDING_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_pending(folder: Path, records: dict) -> None:
    try:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / PENDING_FILE, "w", encoding="utf-8") as handle:
            json.dump(records, handle, indent=2)
    except OSError:
        # Losing the note costs a resubmission, not the run.
        pass

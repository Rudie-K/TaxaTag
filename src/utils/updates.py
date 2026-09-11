# src/utils/updates.py
"""
Noticing that something newer exists, and remembering what was said about it.

Two quite different things can be out of date, and they are kept apart on
purpose. The **application** updates by running an installer, which replaces
files this process is executing from - so it cannot be done from inside the
running program, only handed to something else on the way out. A **library**
updates by downloading and unpacking an archive, which `reference/download.py`
already does safely while TaxaTag keeps running.

What they share is the awkward part, which is not the downloading. It is
knowing when *not* to ask. An updater that asks every launch is worse than
none: people learn to dismiss it without reading, and then miss the release
that mattered. So every offer is remembered against the version it was about,
and `should_offer` is the one place that decides.

Nothing here raises on a network failure. A user with no connection, or
behind a proxy, or on a train, is not having a problem that an error message
would help with - they are simply not being offered an update today.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: The two answers that are remembered. "Update" is not among them: it
#: either happens, in which case the version changes and there is nothing
#: left to ask about, or it fails, in which case asking again is correct.
LATER = "later"
NEVER = "never"

#: Where the application's own releases are published. Private today, which
#: means `application_update` returns None for everybody - see the module
#: docstring in `tools/make_library_archive.py` for the parallel decision
#: about content.
APPLICATION_REPOSITORY = "Rudie-K/TaxaTag"

#: How long to wait on GitHub before giving up. Short on purpose: this runs
#: at startup, and a slow answer is worth less than a fast window.
TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class Update:
    """
    Something newer than what is installed, and everything needed to get it.

    `channel` is what the answer is remembered against: `"application"`, or a
    library's key. It is what makes "don't ask again about this library"
    different from "don't ask again about TaxaTag".
    """

    channel: str
    name: str
    version: str
    url: str
    sha256: str
    landing_page: str = ""
    size_bytes: int = 0

    def describe_size(self) -> str:
        if not self.size_bytes:
            return ""
        return f"{self.size_bytes / 1_000_000:.0f} MB"


@dataclass(frozen=True)
class Answer:
    """
    What was last said about a channel, and which version it was said about.

    The version is the half that matters and the half that is easy to leave
    out. "Don't ask again" without it means *never ask again about anything*,
    which is not what anybody means when they click it - they mean "not this
    one". Storing the version is what makes the next one askable.
    """

    decision: str
    version: str


# --------------------------------------------------------------- versions

#: A version as people write it, with an optional leading v and optional
#: trailing text: `1.0.0`, `v1.2`, `2.0.0-beta`.
_NUMBERS = re.compile(r"\d+")


def parse_version(text: str) -> Tuple[int, ...]:
    """
    A version as a tuple of numbers, for comparing.

    Deliberately forgiving. A tag may be `v1.0.0` or `1.0.0`, and a release
    that arrives as `1.0` should still compare sensibly against `1.0.0` -
    which it does, because `(1, 0)` sorts below `(1, 0, 0)` only when the
    lengths differ, and equal-length prefixes compare first.

    Text that holds no numbers at all returns an empty tuple, which sorts
    below everything. That is the safe direction: an unparseable version is
    never treated as newer than what is installed.
    """
    return tuple(int(found) for found in _NUMBERS.findall(text or ""))


def is_newer(candidate: str, than: str) -> bool:
    """Whether `candidate` is a later version than `than`."""
    left, right = parse_version(candidate), parse_version(than)
    if not left:
        return False
    # Pad so that 1.0 and 1.0.0 compare as equal rather than as different.
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return left > right


#: A version, as opposed to anything else that might be in the field: an
#: optional v, then numbers separated by dots, and nothing more.
_A_VERSION = re.compile(r"^v?\d+(\.\d+)*$", re.IGNORECASE)


def orderable(text: str) -> bool:
    """
    Whether this identifier can be said to be earlier or later than another.

    Not every channel has versions. A library has no version number of its
    own, so its "version" here is the checksum of the archive it came from -
    and two checksums can be compared for equality but never for order.
    Asking which of two hashes is newer has no answer, and any code that
    appears to give one is giving a wrong one.
    """
    return bool(_A_VERSION.match((text or "").strip()))


# ------------------------------------------------------- what was answered


def record_path() -> Path:
    """
    Where answers are kept: beside the settings, not beside the program.

    A user may not be able to write next to an installed executable, and an
    answer is theirs rather than the installation's - two accounts on one
    machine should be able to disagree about whether they want to be asked.
    """
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return Path(base) / "TaxaTag" / "updates.json"


def remembered() -> Dict[str, Answer]:
    """
    Every answer on file, by channel.

    A missing, unreadable or malformed file is the same as no answers. This
    is a preference, not data: losing it costs one extra question.
    """
    try:
        raw = json.loads(record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}

    answers: Dict[str, Answer] = {}
    for channel, value in raw.items():
        if not isinstance(value, dict):
            continue
        decision = value.get("decision")
        if decision in (LATER, NEVER):
            answers[str(channel)] = Answer(decision, str(value.get("version", "")))
    return answers


def remember(channel: str, decision: str, version: str) -> None:
    """Write down what was said, replacing anything said before."""
    if decision not in (LATER, NEVER):
        raise ValueError(f"not an answer that can be remembered: {decision!r}")

    answers = remembered()
    answers[channel] = Answer(decision, version)
    path = record_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    name: {"decision": answer.decision, "version": answer.version}
                    for name, answer in sorted(answers.items())
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        # Not being able to remember is not worth interrupting anybody for.
        # The cost is being asked again next time, which is the old
        # behaviour rather than a new fault.
        pass


def forget(channel: str) -> None:
    """Drop the answer for one channel, so the next offer is made again."""
    answers = remembered()
    if answers.pop(channel, None) is None:
        return
    path = record_path()
    try:
        path.write_text(
            json.dumps(
                {
                    name: {"decision": answer.decision, "version": answer.version}
                    for name, answer in sorted(answers.items())
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


# ------------------------------------------------------------ the decision


def should_offer(update: Update, answer: Optional[Answer]) -> bool:
    """
    Whether to put this update in front of the user now.

    The whole nagging/silence trade-off lives here, and both failure modes
    are real. Ask too often and the dialog becomes something people dismiss
    without reading, so the release that actually matters is dismissed too.
    Ask too rarely and a user sits on a version with a known fault for
    months, believing they are current.

    What the three buttons are meant to mean:

      - **Update** is not remembered. It either succeeds, and the installed
        version changes so there is nothing left to ask about, or it fails,
        and asking again is right.
      - **Remind me later** comes back on the next start. It means "not
        now", not "not this version".
      - **Don't ask again** is about the version on offer, not about
        updates in general. Something newer than the thing they refused is
        a thing they have not refused.

    `answer` is what is on file for this channel, or None if nothing is.
    """
    if answer is None:
        return True

    if answer.decision == LATER:
        return True

    # From here it is NEVER, which is about the version they refused and not
    # about the channel. The question is whether what is on offer now is
    # something they have not already said no to.
    if orderable(update.version) and orderable(answer.version):
        # Strictly newer, not merely different. If a release is withdrawn and
        # an older one becomes latest, that is not something new since they
        # refused - it is the same refusal or less, and asking again would be
        # exactly the nagging this button exists to stop.
        return is_newer(update.version, answer.version)

    # A checksum, which has no order. Any different publication is one they
    # have not refused; the same one is the one they did.
    return update.version != answer.version


# ------------------------------------------------------- what is out there


def _release(repository: str) -> Optional[dict]:
    """
    The newest published release of a repository, or None.

    None covers every uninteresting case at once: no network, a private or
    missing repository, a rate limit, a draft-only release. None of these is
    something a user asked about, so none of them produces a message.
    """
    import urllib.error
    import urllib.request

    url = f"https://api.github.com/repos/{repository}/releases/latest"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            found = json.load(response)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return found if isinstance(found, dict) else None


def _checksums(assets: List[dict]) -> Dict[str, str]:
    """
    The checksums published beside a release, by file name.

    A release carries a `CHECKSUMS.txt` in the same format `sha256sum`
    writes, which is what `tools/make_library_archive.py` produces. Reading
    it from the release rather than hard-coding it is the only option for
    the application, whose future versions cannot be known in advance - so
    the trust anchor is HTTPS to a pinned repository, which is what every
    other updater rests on too.
    """
    import urllib.error
    import urllib.request

    for asset in assets:
        if asset.get("name", "").upper() != "CHECKSUMS.TXT":
            continue
        try:
            with urllib.request.urlopen(
                asset.get("browser_download_url", ""), timeout=TIMEOUT_SECONDS
            ) as response:
                text = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return {}
        found = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and len(parts[0]) == 64:
                found[parts[1].lstrip("*")] = parts[0].lower()
        return found
    return {}


def application_update(installed: str = "") -> Optional[Update]:
    """
    A newer TaxaTag than the one running, if there is one published.

    Returns None unless there is a release, it is newer, it carries a
    Windows installer, and that installer has a published checksum. The
    last condition is not optional: this ends in running a downloaded
    executable, and running one that could not be verified would be a
    worse fault than never updating at all.
    """
    from src.version import __version__

    installed = installed or __version__
    release = _release(APPLICATION_REPOSITORY)
    if not release:
        return None

    version = str(release.get("tag_name", "")).lstrip("vV")
    if not is_newer(version, installed):
        return None

    assets = [a for a in release.get("assets", []) if isinstance(a, dict)]
    sums = _checksums(assets)
    for asset in assets:
        name = asset.get("name", "")
        if not name.lower().endswith(".exe"):
            continue
        digest = sums.get(name, "")
        if not digest:
            continue
        return Update(
            channel="application",
            name="TaxaTag",
            version=version,
            url=asset.get("browser_download_url", ""),
            sha256=digest,
            landing_page=release.get("html_url", ""),
            size_bytes=int(asset.get("size", 0)),
        )
    return None


def library_updates() -> List[Update]:
    """
    Installed libraries that have been republished since they were fetched.

    The comparison is by checksum, not by version: a library has no version
    number of its own, and re-publishing the same name with different
    contents is exactly the case that matters. `install_record` beside the
    library says what was fetched; the catalogue says what is offered now.
    """
    from src.reference import download

    found: List[Update] = []
    for entry in download.offerable():
        installed = download.installed_at(entry)
        if installed is None:
            continue                              # not installed, not an update
        record = install_record(installed)
        was = str(record.get("sha256", "")).lower()
        if not was or was == entry.sha256.lower():
            continue
        found.append(
            Update(
                channel=entry.key,
                name=entry.name,
                version=str(record.get("published", "")) or entry.sha256[:12],
                url=entry.url,
                sha256=entry.sha256,
                landing_page=entry.landing_page,
                size_bytes=entry.size_bytes,
            )
        )
    return found


#: Written beside a library when it is installed, so that a later run can
#: tell which published archive it came from.
INSTALL_RECORD = ".taxatag-source.json"


def install_record(folder: Path) -> dict:
    """What a library folder says about where it came from; {} if it is silent."""
    try:
        raw = json.loads((Path(folder) / INSTALL_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_install_record(folder: Path, entry) -> None:
    """
    Record which published archive a library folder was unpacked from.

    Without this a downloaded library is indistinguishable from one built
    locally, and neither can be told apart from a newer republication of
    itself - so there would be nothing to compare and no update to notice.
    """
    try:
        (Path(folder) / INSTALL_RECORD).write_text(
            json.dumps(
                {
                    "key": entry.key,
                    "name": entry.name,
                    "sha256": entry.sha256.lower(),
                    "url": entry.url,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def pending() -> List[Update]:
    """
    Everything worth asking about right now, application first.

    This is what the window calls at startup. It applies `should_offer`, so
    anything the user has already refused is absent rather than filtered out
    later - there is one place that decides, and this is the caller of it.
    """
    answers = remembered()
    updates: List[Update] = []
    application = application_update()
    if application is not None:
        updates.append(application)
    updates.extend(library_updates())
    return [u for u in updates if should_offer(u, answers.get(u.channel))]

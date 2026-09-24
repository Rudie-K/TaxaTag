"""
Fetching a reference library instead of building one.

Building a library is a long job with tens of gigabytes of intermediate
files, and it is the right answer for somebody making a library nobody else
has. It is the wrong answer for the far more common case: an ecologist who
wants the marine core, has never used a command line, and would reasonably
give up rather than download four archives from four websites and run a
build overnight.

So the library is offered as a download, from a public archive, from inside
the application.

**The failure this module exists to prevent is a download that half worked.**
Two gigabytes over a domestic connection is long enough to be interrupted,
and every interesting way it goes wrong produces a file rather than an error:
a truncated archive, a proxy's error page saved under the right name, a disk
that filled at ninety per cent. Every one of those extracts into something
that looks like a library. BLAST will search it happily and return answers
that are wrong in ways nobody will trace back to here.

The defences, in the order they apply:

* **A checksum is required before anything is offered.** An entry without
  one is not published, no matter how complete it otherwise looks. This is
  the one rule with no exception, because it is the only check that can tell
  a good file from a plausible one.
* **The download resumes rather than restarts.** A dropped connection at
  ninety per cent should cost seconds, not the whole evening - otherwise the
  user's next move is to give up.
* **Nothing touches the real folder until the checksum passes.** The archive
  arrives as a `.part`, is verified, and is extracted into a staging folder
  that is moved into place at the end. An interrupted download must never be
  able to damage a library that was already working.
* **Space is checked first.** Failing at ninety per cent because the disk
  filled is the same cost as failing at the start, and much more annoying.

The terms travel with the data. `licences.write_notice` writes the sources
and what they permit into the extracted folder, so a library copied onto a
memory stick two years from now still says where it came from.
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

#: Read and hashed a megabyte at a time. Large enough that the syscalls do
#: not dominate, small enough that progress moves visibly and a cancel is
#: acted on promptly.
CHUNK_BYTES = 1024 * 1024

#: The partly-downloaded archive. Kept between attempts on purpose: it is
#: what makes a resume possible, and it is named so that anybody who finds
#: one in their data folder can see what it is.
PART_SUFFIX = ".part"

#: Where an archive is unpacked before it is moved into place.
STAGING_NAME = ".incoming"

#: Left free after an install, for the machine's sake rather than ours. A
#: fixed figure rather than a percentage: what Windows needs to keep working
#: does not scale with the size of the library being downloaded.
HEADROOM_BYTES = 1024 * 1024 * 1024

#: Progress is reported as (bytes so far, bytes expected, what is happening).
#: Total may be 0 when a server declines to say, which is why the caller is
#: given it rather than a percentage it might divide by zero.
Progress = Callable[[int, int, str], None]

#: Asked between chunks; returning True stops the download tidily, leaving
#: the .part file so the next attempt resumes rather than restarts.
ShouldStop = Callable[[], bool]


class DownloadProblem(RuntimeError):
    """
    A download that cannot be trusted, with a reason a user can act on.

    Deliberately one class. Every failure here has the same remedy from the
    user's side - try again, or check the disk - and distinguishing them in
    the type would only tempt a caller into handling some and not others.
    """


@dataclass(frozen=True)
class DownloadableLibrary:
    """
    A reference library that can be fetched rather than built.

    `size_bytes` is the archive, and `installed_bytes` what it becomes once
    unpacked. Both are needed: one sizes the download, the other decides
    whether it will fit, and for a compressed BLAST library they differ by
    enough that guessing one from the other is not safe.
    """

    key: str
    name: str
    description: str
    size_bytes: int
    installed_bytes: int
    #: Which reference sources went into it, for the licence notice.
    sources: Tuple[str, ...]
    #: Empty until the archive is actually published somewhere.
    url: str = ""
    sha256: str = ""
    #: A citable identifier, which matters to the people who will use this
    #: in a methods section. Zenodo issues one per version.
    doi: str = ""
    landing_page: str = ""

    @property
    def published(self) -> bool:
        """
        Whether this may be offered to anybody.

        Both halves are required, and the checksum is the one that matters:
        an entry with a URL and no checksum describes a download that cannot
        be verified, which is worse than no download at all because it looks
        like a feature.
        """
        return bool(self.url) and bool(self.sha256)

    def describe_size(self) -> str:
        """The two numbers a user needs before deciding, in plain units."""
        return (
            f"{self.size_bytes / 1_000_000_000:.1f} GB to download, "
            f"{self.installed_bytes / 1_000_000_000:.1f} GB once installed"
        )


#: Every library TaxaTag knows how to fetch.
#:
#: The marine core is the one that makes TaxaTag usable out of the box. It
#: is free, and the intention is that it stays free; that it is *offered*
#: here rather than bundled is a size decision, not a licensing one.
AVAILABLE: Dict[str, DownloadableLibrary] = {
    "marine-core": DownloadableLibrary(
        key="marine-core",
        name="Marine core",
        description=(
            "Fish, invertebrates and eukaryotic plankton for the four "
            "markers TaxaTag ships primers for: 12S, 16S, COI and 18S. "
            "Enough to identify a typical coastal or offshore survey "
            "offline, in seconds rather than hours."
        ),
        # Both measured rather than estimated: the archive is the size
        # GitHub reports for the asset, and the installed figure is the 55
        # files it unpacks to. They differ by more than three times, which
        # is why `enough_space` asks for the second and not the first.
        #
        # Edition 2026-09-24: the first edition with 0028's names, 0041's
        # genes and 0042's taxonomy (`docs/changelog/library-marine-core-
        # 2026-09-24.md`). Whoever installed the first edition is offered
        # this one, because `updates.library_updates` compares checksums.
        size_bytes=665_532_526,
        installed_bytes=2_182_326_747,
        sources=("MIDORI2", "MitoFish", "BOLD", "PR2", "NCBI"),
        # Hosted as a release asset rather than a committed file: GitHub
        # refuses any tracked file over 100 MB, while a release asset may
        # be 2 GB. The URL redirects to a signed, short-lived CDN address,
        # so whatever fetches it must follow redirects.
        url=(
            "https://github.com/Rudie-K/TaxaTag-additional-content"
            "/releases/download/v1.1.0-marine-core/taxatag-marine-core-2026-09-24.zip"
        ),
        sha256="6a828dffaa081dda58922f278062097b3850e8a93e85a5be5f7edd0ad80c8f5f",
        # No Zenodo deposit yet, so no DOI to cite. The landing page is
        # somewhere a reader can at least see what the archive contains.
        doi="",
        landing_page=(
            "https://github.com/Rudie-K/TaxaTag-additional-content"
            "/releases/tag/v1.1.0-marine-core"
        ),
    ),
}


def offerable() -> List[DownloadableLibrary]:
    """
    The libraries that may actually be offered, which may be none.

    Separate from `AVAILABLE` so that an entry can be written, reviewed and
    committed before its archive exists, without any risk of the interface
    offering a download that would fail.
    """
    return [entry for entry in AVAILABLE.values() if entry.published]


def install_root() -> Path:
    """
    Where a downloaded library is put.

    The user's own data folder, which is the first place `library.discover`
    looks and the copy that survives an update - unlike anything written
    beside the executable, which an installer is entitled to replace.
    """
    from src.reference.library import LIBRARY_FOLDER
    from src.utils.platform import user_data_dir

    return user_data_dir() / LIBRARY_FOLDER


def installed_at(entry: DownloadableLibrary) -> Optional[Path]:
    """The folder this library occupies, if it is already there."""
    from src.reference.library import is_library

    folder = install_root() / entry.key
    return folder if is_library(folder) else None


def space_needed(entry: DownloadableLibrary) -> int:
    """
    Free bytes required before a download should be started.

    Both the archive and what it unpacks to exist at the same time, since
    the archive cannot be deleted until the extraction has succeeded. Sizing
    this from the installed figure alone is the mistake that fails at the
    last step, with everything downloaded.

    The margin on top is for the machine rather than for us. Filling a disk
    to the last byte leaves Windows unable to grow a page file or write a
    temporary file, and the symptoms turn up in unrelated programs - so a
    download that technically fitted would be blamed for a broken computer.
    """
    return entry.size_bytes + entry.installed_bytes + HEADROOM_BYTES


def enough_space(entry: DownloadableLibrary, folder: Optional[Path] = None) -> Tuple[bool, str]:
    """
    Whether `folder` has room, and what to say if it does not.

    Checked before the first byte, because a disk that fills at ninety per
    cent costs the whole download and tells the user nothing they could not
    have been told at the start.
    """
    target = Path(folder) if folder else install_root()
    probe = target
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent

    try:
        free = shutil.disk_usage(probe).free
    except OSError as problem:
        # Not a refusal. If the free space cannot be read the download is
        # still worth attempting; it will fail honestly if the disk is full.
        return True, f"Could not check free space on {probe} ({problem})."

    needed = space_needed(entry)
    if free >= needed:
        return True, ""
    return False, (
        f"Not enough room on {probe}. This needs about "
        f"{needed / 1_000_000_000:.1f} GB free while it installs, and there "
        f"is {free / 1_000_000_000:.1f} GB. The archive and the unpacked "
        "library both exist until the last step, so the peak is larger than "
        "the finished library."
    )


def _session():
    """An HTTP session that retries, following what ncbi_remote already does."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(
        total=5, backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


def fetch_archive(
    entry: DownloadableLibrary,
    into: Path,
    progress: Optional[Progress] = None,
    should_stop: Optional[ShouldStop] = None,
) -> Optional[Path]:
    """
    Download the archive to `into`, resuming a previous attempt if there is one.

    Returns the finished archive, or None if it was stopped. Raises
    `DownloadProblem` if what arrived is not what was expected.

    The resume is a byte-range request against the `.part` file already on
    disk. A server that ignores the range header sends the whole file again,
    which is handled by watching the status code rather than trusting it to
    behave - 206 means the range was honoured, 200 means start again.
    """
    if not entry.published:
        raise DownloadProblem(
            f"{entry.name} is not published yet, so there is nothing to download."
        )

    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)
    part = into / (entry.key + PART_SUFFIX)
    already = part.stat().st_size if part.exists() else 0

    headers = {"Range": f"bytes={already}-"} if already else {}
    session = _session()
    try:
        response = session.get(entry.url, headers=headers, stream=True, timeout=60)
        response.raise_for_status()

        # 200 to a range request means the server sent everything from the
        # start, so anything already downloaded is not a prefix of what is
        # arriving now and keeping it would corrupt the file.
        if already and response.status_code != 206:
            already = 0
            part.unlink(missing_ok=True)

        declared = int(response.headers.get("Content-Length") or 0)
        total = already + declared if declared else entry.size_bytes

        done = already
        with open(part, "ab" if already else "wb") as handle:
            for block in response.iter_content(chunk_size=CHUNK_BYTES):
                if should_stop and should_stop():
                    # The .part file stays, which is the point of stopping
                    # tidily rather than deleting the evening's progress.
                    return None
                handle.write(block)
                done += len(block)
                if progress:
                    progress(done, total, f"Downloading {entry.name}")
    except OSError as problem:
        raise DownloadProblem(f"Could not write the download: {problem}") from problem
    except Exception as problem:                      # requests' own errors
        raise DownloadProblem(
            f"The download did not finish: {problem}. Nothing has been lost - "
            "starting it again will carry on from where it stopped."
        ) from problem
    finally:
        session.close()

    return part


def verify(
    archive: Path,
    expected_sha256: str,
    progress: Optional[Progress] = None,
    should_stop: Optional[ShouldStop] = None,
) -> bool:
    """
    Whether `archive` is byte-for-byte what was published.

    The one check that distinguishes a good download from a plausible one.
    A truncated archive, a proxy's error page and a corrupted disk all
    produce files; only this tells them apart.
    """
    digest = hashlib.sha256()
    size = archive.stat().st_size
    done = 0
    with open(archive, "rb") as handle:
        while True:
            block = handle.read(CHUNK_BYTES)
            if not block:
                break
            if should_stop and should_stop():
                return False
            digest.update(block)
            done += len(block)
            if progress:
                progress(done, size, "Checking the download")
    return digest.hexdigest().lower() == expected_sha256.strip().lower()


def install(
    entry: DownloadableLibrary,
    progress: Optional[Progress] = None,
    should_stop: Optional[ShouldStop] = None,
) -> Optional[Path]:
    """
    Download, verify, unpack and put a library in place.

    Returns the installed folder, or None if it was stopped. The order is
    the whole design: nothing that already worked is touched until a
    verified archive has been unpacked successfully somewhere else.
    """
    from src.reference import licences
    from src.reference.library import is_library
    from src.utils import updates

    root = install_root()
    fits, why = enough_space(entry, root)
    if not fits:
        raise DownloadProblem(why)

    archive = fetch_archive(entry, root, progress=progress, should_stop=should_stop)
    if archive is None:
        return None

    if progress:
        progress(0, archive.stat().st_size, "Checking the download")
    if should_stop and should_stop():
        return None
    if not verify(archive, entry.sha256, progress=progress, should_stop=should_stop):
        # Deleted rather than kept. A resume assumes what is on disk is a
        # correct prefix, and a file that failed its checksum is evidence
        # against exactly that - so resuming it would fail forever.
        archive.unlink(missing_ok=True)
        raise DownloadProblem(
            "The download arrived damaged and has been discarded. This is "
            "usually a connection that dropped quietly. Try again."
        )

    staging = root / STAGING_NAME
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        if progress:
            progress(0, 0, "Unpacking")
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(staging)
    except (OSError, zipfile.BadZipFile) as problem:
        shutil.rmtree(staging, ignore_errors=True)
        raise DownloadProblem(f"The archive could not be unpacked: {problem}") from problem

    # An archive may or may not have a folder at the top. Both are normal,
    # and which one it is should not be something the user has to know.
    unpacked = staging
    if not is_library(unpacked):
        children = [child for child in staging.iterdir() if child.is_dir()]
        found = [child for child in children if is_library(child)]
        if len(found) != 1:
            shutil.rmtree(staging, ignore_errors=True)
            raise DownloadProblem(
                "The archive unpacked, but there is no reference library "
                "inside it. This is a problem with the published file rather "
                "than with your download."
            )
        unpacked = found[0]

    licences.write_notice(unpacked, entry.sources)
    # Written before the move, so what lands in place is already complete.
    # Without it a downloaded library cannot be told from a locally built
    # one, nor from a newer republication of itself - so there would be
    # nothing to compare and no update to notice. See `utils/updates.py`.
    updates.write_install_record(unpacked, entry)

    final = root / entry.key
    try:
        if final.exists():
            replaced = root / (entry.key + ".replaced")
            shutil.rmtree(replaced, ignore_errors=True)
            final.rename(replaced)
        unpacked.rename(final)
        shutil.rmtree(root / (entry.key + ".replaced"), ignore_errors=True)
    except OSError as problem:
        shutil.rmtree(staging, ignore_errors=True)
        raise DownloadProblem(
            f"The library downloaded but could not be moved into place: {problem}"
        ) from problem

    shutil.rmtree(staging, ignore_errors=True)
    archive.unlink(missing_ok=True)
    if progress:
        progress(1, 1, "Done")
    return final

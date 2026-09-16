# src/reference/library.py
"""
The local reference library.

A reference library is a folder holding two things that work together:

    taxatag_reference_core.db    a SQLite catalogue: for every reference
                                 sequence, which marker it belongs to and its
                                 full taxonomic lineage
    blast_volumes/<marker>/      a BLAST database per marker, holding the
                                 sequences themselves

The split matters. SQLite is excellent at "given this identifier, tell me the
species and its lineage" - an indexed lookup over tens of millions of rows in
well under a millisecond. It cannot align sequences at all, which is what
identification actually requires. BLAST does the alignment and hands back an
identifier; SQLite turns that identifier into biology. Neither replaces the
other.

Searching a library on this computer instead of NCBI's servers is roughly
four orders of magnitude faster, works with no internet connection, and gives
the same answer every time - which a shared queue on someone else's servers
does not.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from src.reference import markers as markers_module

#: The catalogue file inside a reference library folder.
CATALOGUE_NAME = "taxatag_reference_core.db"

#: The folder holding one BLAST database per marker.
VOLUMES_DIR = "blast_volumes"

#: Lineage columns in the catalogue, in the order a biologist reads them.
#: "order" is a reserved word in SQL, hence the column name.
LINEAGE_COLUMNS = [
    ("kingdom", "Kingdom"),
    ("phylum", "Phylum"),
    ("class", "Class"),
    ("order_rank", "Order"),
    ("family", "Family"),
    ("genus", "Genus"),
    ("species", "Species"),
]

#: Bare values the catalogue uses to mean "not known".
UNKNOWN_VALUES = {"", "na", "n/a", "none", "null", "unclassified", "environmental sample"}

#: Placeholders are also written per rank - "Unknown_Family", "Unassigned
#: Genus", "Unknown Species" - so the prefix is matched rather than every
#: combination being listed. Without this, a results table ends up with
#: "Unknown_Order" sitting in the Order column as though it were a real taxon.
UNKNOWN_PREFIXES = ("unknown", "unassigned", "incertae sedis")

#: PR2 marks a rank it could not resolve by repeating the nearest named
#: ancestor with an X appended per rank descended, so an unresolved diatom
#: becomes "Bacillariophyceae_XXX" in the genus column. That is not a genus,
#: but read as one it disagrees with every properly named reference it ties
#: against - and a perfect match to Thalassionema was discarded for exactly
#: that reason, because one of its four tied references was an unresolved
#: diatom. Nearly a quarter of the 18S library carries one of these.
UNRESOLVED_RANK = re.compile(r"_X+$", re.IGNORECASE)

#: "Thalassionema sp." is not a species. It means a Thalassionema whose
#: species is unknown, and treating it as a name makes it contradict
#: "Thalassionema frauenfeldii" when the two do not actually disagree - one is
#: simply less resolved. The genus is still carried in the genus column, so
#: nothing is lost by declining to read this as a species. Nearly half of the
#: 18S library is written this way.
UNRESOLVED_SPECIES = re.compile(r"\s+(sp|spp|sp\.|spp\.)$", re.IGNORECASE)

#: The same idea, further into the name. GenBank writes "Protopterus sp.
#: NBE-2020", "Chiloglanis aff. micropogon 8 JD-2023a", "Cichla cf. monoculus
#: JFR-2006": a genus known, a species provisional, and a voucher code after
#: it, so the rule above (which looks only at the end) let all of these vote
#: as species - 6,476 of them in the 12S volume, each a string no other
#: record shares, each a vote against the real binomial in a tie. And
#: "androgenetic Carassius auratus red var. x Megalobrama amblycephala" is a
#: hybrid, which is not a species of anything: 645 of those. Decision 0028;
#: Sussex Audit issues log 33.
PROVISIONAL_SPECIES = re.compile(r"\s(sp|spp|cf|aff|nr)\.?(\s|$)", re.IGNORECASE)
HYBRID = re.compile(r"\s+x\s+")


def is_unknown(value: Optional[str]) -> bool:
    """True when a catalogue field is a placeholder rather than a real name."""
    if value is None:
        return True
    text = str(value).strip().lower()
    if text in UNKNOWN_VALUES:
        return True
    if UNRESOLVED_RANK.search(text) or UNRESOLVED_SPECIES.search(text):
        return True
    if PROVISIONAL_SPECIES.search(text) or HYBRID.search(text):
        return True
    return any(
        text == prefix or text.startswith(prefix + "_") or text.startswith(prefix + " ")
        for prefix in UNKNOWN_PREFIXES
    )


def clean(value: Optional[str]) -> str:
    """A catalogue field as it should appear in results."""
    return "" if is_unknown(value) else str(value).strip()


@dataclass
class ReferenceRecord:
    """What the catalogue knows about one reference sequence."""

    accession: str
    marker: str = ""
    lineage: Dict[str, str] = field(default_factory=dict)
    source_accession: str = ""

    @property
    def best_name(self) -> str:
        """
        The most precise name available for this reference.

        Falls back up the lineage rather than reporting nothing, so a sequence
        identified only to family is still reported as that family.
        """
        for column, _ in reversed(LINEAGE_COLUMNS):
            value = self.lineage.get(column, "")
            if value:
                return value
        return ""

    @property
    def finest_rank(self) -> str:
        """Which rank `best_name` came from."""
        for column, label in reversed(LINEAGE_COLUMNS):
            if self.lineage.get(column, ""):
                return label
        return ""


@dataclass
class MarkerVolume:
    """One marker's BLAST database within a library."""

    marker: str
    path: Path          # database name, without file extensions
    sequences: int = 0

    @property
    def exists(self) -> bool:
        # A BLAST database is a family of files sharing one prefix; none of
        # them is at the prefix path itself.
        return bool(_glob(self.path.parent, self.path.name + ".*"))


class ReferenceLibrary:
    """Read access to a reference library folder."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.catalogue_path = self.root / CATALOGUE_NAME
        self._connection: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    @property
    def exists(self) -> bool:
        return _exists(self.catalogue_path)

    def volume(self, marker: str, scope: str = "") -> MarkerVolume:
        """
        Where a marker's BLAST database lives, whether or not it is there.

        A `scope` names an alias database restricting the volume to a subset
        of its references - see `src/reference/scopes.py` and decision 0006.
        The path is returned whether or not the alias has been built, because
        a caller that asked for a scope needs to be able to tell the
        difference and say so; silently searching the whole volume instead
        would look like the scope had been applied.
        """
        marker_info = markers_module.MARKERS.get(marker)
        name = marker_info.volume_name if marker_info else f"local_{marker}_db"
        if scope:
            name = f"{name}_{scope}"
        return MarkerVolume(marker, self.root / VOLUMES_DIR / marker / name)

    def scopes(self, marker: str) -> List[str]:
        """Which scopes have been built over one marker's volume."""
        full = self.volume(marker)
        prefix = full.path.name + "_"
        return sorted(
            path.stem[len(prefix):]
            for path in _glob(full.path.parent, prefix + "*.nal")
        )

    def scoped_markers(self, scope: str) -> List[str]:
        """Markers this scope has actually been built for."""
        if not scope:
            return self.available_markers()
        return [
            marker for marker in self.available_markers()
            if self.volume(marker, scope).exists
        ]

    def available_markers(self) -> List[str]:
        """Markers that have both a BLAST volume and catalogue entries."""
        found = []
        for marker in markers_module.known_markers():
            if self.volume(marker).exists:
                found.append(marker)
        # A library may hold a marker TaxaTag does not know about yet.
        volumes_root = self.root / VOLUMES_DIR
        if _exists(volumes_root):
            for folder in _iterdir(volumes_root):
                is_dir, _ = _is_directory(folder)
                if is_dir and folder.name not in found:
                    if _glob(folder, "*.n*"):
                        found.append(folder.name)
        return found

    # ------------------------------------------------------------------
    # Catalogue
    # ------------------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        """
        Open the catalogue read-only.

        Read-only matters: an analysis must never be able to damage a
        reference library that took hours to build, and it lets several runs
        share one library on a network drive without locking each other out.
        """
        if self._connection is None:
            if not self.exists:
                raise FileNotFoundError(f"No reference catalogue at {self.catalogue_path}")
            uri = f"file:{self.catalogue_path.as_posix()}?mode=ro"
            self._connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def coverage(self, marker: str, species: str, genus: str = "") -> Dict[str, int]:
        """
        How well placed this library was to name a species: references for
        the species itself, references for its genus, and how many species
        of that genus the library holds at all.

        Bourret et al. (2023) grade a species assignment *unreliable due to
        gaps* when the library lacks its congeners - with one congener
        sequenced there is nothing for the barcode to be ambiguous about,
        and the wrong name wins cleanly. These three counts are that grade's
        raw material, from the catalogue alone; `docs/science/`, section 3.
        """
        empty = {"species_references": 0, "genus_references": 0, "genus_species": 0}
        try:
            connection = self.connect()
        except (FileNotFoundError, sqlite3.Error):
            return empty
        genus = genus or species.split(" ")[0]
        try:
            for_species = connection.execute(
                "SELECT COUNT(*) FROM reference_library WHERE marker_gene = ? AND species = ?",
                (marker, species),
            ).fetchone()[0]
            for_genus, distinct = connection.execute(
                "SELECT COUNT(*), COUNT(DISTINCT species) FROM reference_library "
                "WHERE marker_gene = ? AND genus = ?",
                (marker, genus),
            ).fetchone()
        except sqlite3.Error:
            return empty
        return {"species_references": for_species, "genus_references": for_genus,
                "genus_species": distinct}

    def counts_by_marker(self) -> Dict[str, int]:
        """How many reference sequences the catalogue holds for each marker."""
        try:
            cursor = self.connect().execute(
                "SELECT marker_gene, COUNT(*) AS n FROM reference_library GROUP BY marker_gene"
            )
        except (FileNotFoundError, sqlite3.Error):
            return {}
        return {row["marker_gene"]: row["n"] for row in cursor if row["marker_gene"]}

    def total_sequences(self) -> int:
        try:
            row = self.connect().execute("SELECT COUNT(*) FROM reference_library").fetchone()
        except (FileNotFoundError, sqlite3.Error):
            return 0
        return int(row[0]) if row else 0

    @property
    def name(self) -> str:
        """What to call this library in a list, taken from its folder."""
        return self.root.name.replace("_", " ").strip() or str(self.root)

    def describe(self) -> str:
        """
        One line saying what is in it, for someone choosing between libraries.

        Written to be read by someone who knows what 12S is but not what a
        BLAST volume is, so it names markers and counts and nothing else.
        """
        if not self.exists:
            return "not a reference library"
        counts = self.counts_by_marker()
        available = self.available_markers()
        if not available:
            return "catalogue present, but nothing searchable in it"
        parts = [
            f"{marker} ({counts[marker]:,})" if marker in counts else marker
            for marker in available
        ]
        return ", ".join(parts)

    def lookup(self, accessions: Iterable[str]) -> Dict[str, ReferenceRecord]:
        """
        Fetch the lineage behind a set of BLAST hits.

        Looked up in batches with a single indexed query each, because the
        alternative - one query per hit - turns a fast local search into a
        slow one once there are thousands of them.
        """
        wanted = [a for a in dict.fromkeys(accessions) if a]
        if not wanted:
            return {}

        try:
            connection = self.connect()
        except (FileNotFoundError, sqlite3.Error):
            return {}

        columns = ", ".join(column for column, _ in LINEAGE_COLUMNS)
        found: Dict[str, ReferenceRecord] = {}

        # SQLite's default limit is 999 parameters per statement.
        batch_size = 900
        for start in range(0, len(wanted), batch_size):
            batch = wanted[start : start + batch_size]
            placeholders = ",".join("?" * len(batch))
            try:
                rows = connection.execute(
                    f"SELECT accession, marker_gene, common_name, {columns} "
                    f"FROM reference_library WHERE accession IN ({placeholders})",
                    batch,
                ).fetchall()
            except sqlite3.Error:
                continue

            for row in rows:
                found[row["accession"]] = ReferenceRecord(
                    accession=row["accession"],
                    marker=row["marker_gene"] or "",
                    lineage={
                        column: clean(row[column]) for column, _ in LINEAGE_COLUMNS
                    },
                    source_accession=_source_accession(row["common_name"]),
                )
        return found

    # ------------------------------------------------------------------
    # Description, for the interface and the run manifest
    # ------------------------------------------------------------------
    def summary(self) -> dict:
        """What this library contains, in a form worth showing to a user."""
        counts = self.counts_by_marker()
        available = self.available_markers()
        return {
            "root": str(self.root),
            "catalogue": str(self.catalogue_path),
            "exists": self.exists,
            "markers": available,
            "counts": counts,
            "total": sum(counts.values()) if counts else 0,
        }


# ----------------------------------------------------------------------
# Finding the libraries on this machine
# ----------------------------------------------------------------------
#: The folder TaxaTag keeps reference libraries in, both beside the
#: application and in the user's own data folder.
LIBRARY_FOLDER = "reference"


def search_roots() -> List[Path]:
    """
    Where to look for reference libraries, in the order they are offered.

    The user's own data folder comes first because a library they built is
    more likely to be the one they want than one that shipped with the
    application, and because it is the copy that survives an update.

    The last of these matters only in a packaged build, where `app_root` is
    the folder PyInstaller unpacks bin/ and resources/ into - not the folder
    the user can see. Someone putting a library beside TaxaTag.exe, which is
    the obvious thing to do, would otherwise be told there isn't one.
    """
    import sys

    from src.utils import platform as platform_utils

    roots = [
        platform_utils.user_data_dir() / LIBRARY_FOLDER,
        platform_utils.app_root() / LIBRARY_FOLDER,
    ]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).parent / LIBRARY_FOLDER)
    return roots


def catalogue_opens(folder: Path) -> Tuple[bool, Optional[OSError]]:
    """
    Whether the catalogue in `folder` can actually be opened, and if not, why.

    Opened, not stat-ed, because opening the catalogue is the operation the
    pipeline performs - SQLite and BLAST open files; they never ask whether a
    folder is a folder. Judging a library by a question the pipeline never
    asks is how one behind a reparse point got reported as unreadable while
    the self-test used it perfectly well.

    An earlier version of this docstring claimed `open()` succeeds through a
    junction where `is_dir()` is refused. That was wrong, and only looked
    right because it was tested from a shell. A program launched by
    double-click is under Windows' Redirection Guard, and there `open()` is
    refused too - with `[Errno 22]` rather than `WinError 448`, but refused.
    The real fix was to stop putting the library behind a junction at all
    (see `docs/protocols/where-work-lives.md` rule 5); this function is now
    only asked about plain folders, where opening the catalogue is a true
    and cheap test of whether the pipeline will be able to read it.
    """
    catalogue = Path(folder) / CATALOGUE_NAME
    try:
        with open(catalogue, "rb") as handle:
            handle.read(16)
        return True, None
    except (FileNotFoundError, NotADirectoryError):
        # No catalogue there, or `folder` is a file: not a library, and not
        # a refusal either.
        return False, None
    except OSError as problem:
        return False, problem


def is_library(folder: Path) -> bool:
    """True when a folder holds a reference catalogue that can be opened."""
    opens, _ = catalogue_opens(folder)
    return opens


#: Asking the filesystem about a path the user chose can fail, and pathlib
#: hides only some of those failures. `exists()`, `is_dir()`, `glob()`,
#: `iterdir()` and `resolve()` all suppress the errors it considers ordinary
#: - ENOENT, ENOTDIR, EBADF, ELOOP and three Windows equivalents - and
#: re-raise the rest. So every one of them reads like a question and is a
#: statement that may throw.
#:
#: That cost two crashes in a row on the same startup. Guarding one call moved
#: the failure three lines down to the next unguarded one, which is what
#: happens when a class of bug is treated as a list of bugs. These wrappers
#: are the class: below this point, nothing in this module asks the
#: filesystem anything directly.


from src.utils import paths as _paths

#: The safe filesystem questions, shared with the rest of the program.
#: They lived here first, which is why the interface never used them and
#: met the same crash a third time on a different folder. One home now.
_exists = _paths.exists
_iterdir = _paths.iterdir
_glob = _paths.glob
safe_resolve = _paths.resolve


def _describe_refusal(path: Path, problem: OSError) -> str:
    """
    Why a path could not be examined, in words a user can act on.

    WinError 448 gets its own sentence because nothing about the phrase
    Windows uses - "the path cannot be traversed because it contains an
    untrusted mount point" - suggests the actual remedy, and the situation
    reads like corruption when it is a security policy working as intended.
    """
    winerror = getattr(problem, "winerror", None)
    if winerror == 448:
        return (
            f"Windows will not let TaxaTag follow {path.name}, because it is a "
            "shortcut (a junction) into another folder and Windows blocks "
            "installed programs from following shortcuts it did not create. "
            "The library is not damaged. Either move the real folder here "
            "instead of the shortcut, or use 'Choose a folder...' to point "
            "TaxaTag straight at where the data actually lives."
        )
    return f"{path.name} could not be read: {problem}"


def _is_directory(path: Path) -> Tuple[bool, str]:
    """
    Whether `path` is a directory, and why the question could not be answered.

    `Path.is_dir()` reads like a safe question and is not. It suppresses only
    the failures pathlib considers ordinary - ENOENT, ENOTDIR, EBADF, ELOOP
    and three Windows equivalents - and **re-raises everything else**. So a
    call that looks like a predicate is a statement that may throw, which is
    exactly how an unreadable folder became a crash on startup: the error
    left `discover`, passed through `_fill_library_list` and `apply_config`,
    and reached `MainWindow.__init__`, where there is nothing to catch it.

    The case that found this was WinError 448 on a junction, which no test
    would have produced: the same folder is readable from a Python session
    and refused to the installed application, because Windows applies the
    restriction to one and not the other.
    """
    try:
        return path.is_dir(), ""
    except OSError as problem:
        return False, _describe_refusal(path, problem)


def cannot_be_read(path) -> str:
    """
    Why `path` cannot be examined, or "" when it can be.

    For one folder the user named, rather than the search folders scanned by
    `unreadable_paths`. A library chosen by hand can be anywhere, so the
    question has to be askable of an arbitrary path - and "it is not a
    library" and "I am not allowed to look" want different sentences, since
    only the second may fix itself when a drive is plugged back in.
    """
    folder = Path(path)
    opens, problem = catalogue_opens(folder)
    if opens:
        # It works. Whatever the operating system thinks of the folder, the
        # file the pipeline needs came back, and that is the only test that
        # counts. Saying "cannot be read" here would be telling somebody
        # their working library is broken.
        return ""
    if problem is not None:
        return _describe_refusal(folder, problem)
    # No catalogue at all. Only now is the folder itself worth asking about,
    # to separate "not a library" from "not allowed to look".
    _, why = _is_directory(folder)
    return why


def unreadable_paths() -> List[Tuple[Path, str]]:
    """
    Library folders that exist and cannot be examined, and why.

    Distinct from `broken_links`, which is about a target that has gone. This
    is a target that is there and is being refused, and the two need
    different sentences: "restore the folder" is wrong advice for a
    permissions or policy refusal, and would send somebody looking for data
    that was never lost.
    """
    refused: List[Tuple[Path, str]] = []
    for root in search_roots():
        readable, _ = _is_directory(root)
        if not readable:
            continue
        try:
            children = sorted(root.iterdir())
        except OSError as problem:
            refused.append((root, _describe_refusal(root, problem)))
            continue
        for child in children:
            _, why = _is_directory(child)
            if why:
                refused.append((child, why))
    return refused


def broken_links() -> List[Path]:
    """
    Names in the library folders that point at something no longer there.

    A library is often reached through a shortcut, because the data is large
    and lives on whichever drive had room. Delete or move what it points at
    and the shortcut stays: the name is still listed, and everything that asks
    whether it exists is told no.

    The result is a library that vanishes from the list with nothing said. It
    happened here - a folder went to the Recycle Bin from a file manager, and
    the only symptom was an empty dropdown and a self-test that could not find
    anything to search. Naming the shortcut and what it pointed at turns that
    into a one-line answer, and the fix is usually restoring the folder rather
    than anything to do with TaxaTag.
    """
    dangling: List[Path] = []
    for root in search_roots():
        readable, _ = _is_directory(root)
        try:
            children = sorted(root.iterdir()) if readable else []
        except OSError:
            continue
        for child in children:
            # Listed by the directory, but nothing is there when asked
            # directly: the signature of a shortcut whose target has gone.
            #
            # `exists()` suppresses the same short list of failures that
            # `is_dir()` does and re-raises the rest, so it is guarded for
            # the same reason - and a path that is *refused* rather than
            # missing is not reported here at all. It belongs to
            # `unreadable_paths`, because telling somebody to restore a
            # folder that was never lost sends them looking for nothing.
            try:
                if not child.exists():
                    dangling.append(child)
            except OSError:
                continue
    return dangling


def discover(extra: Iterable[Path] = ()) -> List[ReferenceLibrary]:
    """
    Every reference library on this machine, ready to be offered as a choice.

    A search folder may be a library itself, or hold several as subfolders -
    both are normal, because someone with one library keeps it simply and
    someone comparing two keeps them side by side. Folders named in `extra`
    are included wherever they are, which is how a library the user chose by
    hand stays in the list.
    """
    found: Dict[str, ReferenceLibrary] = {}

    def consider(folder: Path) -> None:
        folder = Path(folder)
        resolved = safe_resolve(folder)
        if not is_library(folder):
            return
        # Recognised by where it really is, so the same library reached two
        # ways is offered once - but kept under the name it was reached by.
        # A library is often a shortcut into a folder named something like
        # "database", and the name someone gave the shortcut is the better
        # label of the two.
        if str(resolved) not in found:
            found[str(resolved)] = ReferenceLibrary(folder)

    roots = list(search_roots())
    for named in (Path(p) for p in extra if p):
        # The folder itself, and the one it sits in: someone who points at one
        # library in a folder of libraries means to see the others too, which
        # is the whole reason for keeping more than one.
        roots.append(named)
        if named.parent != named:
            roots.append(named.parent)

    for root in roots:
        consider(root)
        readable, _ = _is_directory(root)
        try:
            children = sorted(root.iterdir()) if readable else []
        except OSError:
            children = []
        for child in children:
            # No is_dir() gate. There was one, added when is_dir() on an
            # unreadable junction crashed start-up - and it then quietly
            # excluded the very library it was protecting against, because
            # the junction refuses is_dir() while allowing the catalogue to
            # be opened. consider() decides by opening the catalogue, which
            # is the only test that matters, and is safe on any path: a
            # file, a refused folder, a shortcut to nowhere.
            consider(child)

    return sorted(found.values(), key=lambda library: library.name.lower())


def _source_accession(common_name: Optional[str]) -> str:
    """
    Recover the originating database's accession from the catalogue.

    The build stored it inside a free-text field, as "MitoFish Acc: PQ550629"
    or "BOLD ID: AANIC003-10". It is the only way back to the original record,
    so it is worth pulling out rather than showing the whole phrase.
    """
    if not common_name:
        return ""
    text = str(common_name)
    for separator in ("Acc:", "ID:", "acc:", "id:"):
        if separator in text:
            return text.split(separator, 1)[1].strip()
    return text.strip()

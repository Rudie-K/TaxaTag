# src/utils/safety.py
"""
Places TaxaTag must never write to, and the check that enforces it.

This exists because of a specific failure. During a re-indexing operation the
16S and COI BLAST volumes were rebuilt under a temporary name, and the files
that were about to become the real volumes were then deleted as though they
were leftovers. They were the only copy. Roughly 1.7 million reference
sequences had to be rebuilt from source.

Two lessons came out of it, and both are enforced here rather than
remembered:

* **A reference drive is read-only by default.** `D:` on this machine holds
  the original data as it was before any of this work, and is the reason the
  loss was recoverable at all. Writing to it is possible, but only when a
  person has said so, in words, for that specific operation - see
  `refuse_if_protected` and the note on confirmation below.
* **A delete must look at what it is deleting.** `remove_matching` refuses to
  remove the last copy of something it cannot see a replacement for.

Neither check is clever. Both are the kind of thing that feels unnecessary
until the day it is not.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional, Sequence


class ProtectedPath(Exception):
    """Raised when something tried to write where it must not."""


#: Roots that hold original data and are never written to.
#:
#: Empty on purpose. Which folder holds somebody's irreplaceable data is a
#: fact about their machine, not about this program, and a path from one
#: person's computer has no meaning in anyone else's copy - it only tells
#: every reader of the source where that person keeps their files.
#:
#: This list started as one hardcoded drive and was emptied before the source
#: was published. Nothing about the protection changed: the roots now come
#: from the two places below, both outside the repository.
DEFAULT_PROTECTED_ROOTS: tuple = ()

#: A file in the user's own data folder, one root per line, blank lines and
#: lines beginning with # ignored. This is where a permanent protection
#: belongs: it survives an update, is not in the repository, and needs no
#: environment variable to be set before every run.
PROTECTED_ROOTS_FILE = "protected_roots.txt"


def _roots_from_file() -> List[Path]:
    """Roots listed in the user's own configuration folder."""
    try:
        from src.utils.platform import user_data_dir

        path = user_data_dir() / PROTECTED_ROOTS_FILE
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ImportError):
        # A protection that cannot be read is not a reason to stop. The
        # caller still gets whatever the environment supplies, and a delete
        # that would have been refused fails loudly elsewhere instead.
        return []
    return [
        Path(line.strip()) for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]


def protected_roots() -> List[Path]:
    """
    Every root that is currently protected.

    Three sources, all additive: nothing here can *remove* a protection, which
    is why there is no environment variable to switch it off. Writing to a
    protected root is possible, but only through the `confirmed` argument to
    `refuse_if_protected`, where a person has to have said so in words.
    """
    roots = [Path(root) for root in DEFAULT_PROTECTED_ROOTS]
    roots += _roots_from_file()
    extra = os.environ.get("TAXATAG_PROTECTED_ROOTS", "")
    roots += [Path(part) for part in extra.split(os.pathsep) if part.strip()]
    return roots


def _normalise(path: Path) -> str:
    """A path in the one form comparisons can rely on."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        resolved = Path(path).absolute()
    return str(resolved).replace("/", os.sep).rstrip(os.sep).lower()


def is_protected(path: Path) -> bool:
    """Whether a path lies inside a root that must not be written to."""
    target = _normalise(path)
    for root in protected_roots():
        base = _normalise(root)
        if target == base or target.startswith(base + os.sep):
            return True
    return False


def refuse_if_protected(path: Path, what: str = "write to", *, confirmed: str = "") -> None:
    """
    Stop before touching a path that holds original data.

    Raises rather than returning a value, because every caller of this is
    about to do something irreversible and none of them should be able to
    carry on by ignoring a return code.

    `confirmed` is the escape hatch, and it is deliberately awkward. It takes
    the words a person actually said agreeing to this particular write - not a
    flag, not True, not a setting. Three things follow from that shape:

    * The agreement is visible at the call site, so anyone reading the code
      later can see who allowed what, and when.
    * It cannot be turned on for everything at once. A caller has to be
      written for the one operation, which is the point: "you may write to D:"
      is not a state the program should ever be in.
    * It cannot arrive from configuration. There is no environment variable
      and no settings key that lifts this, on purpose - a protection that a
      config file can remove protects nothing, because config files are edited
      by whoever is in a hurry. `TAXATAG_PROTECTED_ROOTS` can only *add*
      roots, never take one away.

    The agreement has to be a person's, given for this operation, in words.
    """
    if not is_protected(path):
        return
    if confirmed.strip():
        return
    raise ProtectedPath(
        f"Refusing to {what} {path}.\n"
        "This is inside a protected root - it holds the original data, and "
        "is what makes a mistake elsewhere recoverable.\n"
        "Work on a copy, or, if writing here is genuinely intended, ask the "
        "person whose data it is and pass what they said as `confirmed`.\n"
        f"Protected roots: {', '.join(str(r) for r in protected_roots())}"
    )


def remove_matching(
    folder: Path,
    pattern: str,
    *,
    keep_if_missing: Optional[Sequence[str]] = None,
    what: str = "files",
) -> List[Path]:
    """
    Delete files matching a pattern, unless doing so would leave nothing.

    `keep_if_missing` names patterns whose presence proves the deletion is
    safe - the replacement that the files being deleted were superseded by.
    If none of them matches, nothing is deleted and the caller is told, because
    that is the shape the reference-library loss took: files removed as
    leftovers when the thing they were leftovers of had never been created.
    """
    folder = Path(folder)
    refuse_if_protected(folder, "delete from")

    doomed = sorted(p for p in folder.glob(pattern) if p.is_file())
    if not doomed:
        return []

    if keep_if_missing:
        # A bare string is a sequence of characters, so passing one would look
        # for files called "t", "a", "x"... none of which exists, and the
        # refusal would then fire on a deletion that was perfectly safe. In a
        # module whose whole purpose is refusing at the right moment, an
        # argument that is wrong in a plausible-looking way is worth absorbing
        # rather than documenting.
        if isinstance(keep_if_missing, (str, Path)):
            keep_if_missing = [str(keep_if_missing)]
        replacements = [p for kept in keep_if_missing for p in folder.glob(str(kept))]
        if not replacements:
            raise ProtectedPath(
                f"Refusing to delete {len(doomed)} {what} from {folder}.\n"
                f"They were to be replaced by {', '.join(keep_if_missing)}, "
                "and nothing matching that is there - so these are not "
                "leftovers, they are the only copy."
            )

    for path in doomed:
        path.unlink()
    return doomed

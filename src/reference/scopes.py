# src/reference/scopes.py
"""
Searching part of a reference library instead of all of it.

A library holds one BLAST volume per marker, and a search uses the whole of
it. That is more than any single question needs. A marine survey has no use
for the 104,606 springtails in the COI volume; a run using only MiFish has no
use for references that do not span the MiFish region.

The obvious answer - build a separate database per primer set - copies the
sequences once per set and means rebuilding whenever a set is added. BLAST
offers a better one. An *alias* database names a subset of a real database,
and searching it searches only that subset. Sequences are stored once, and
adding a scope takes seconds rather than a rebuild.

    blast_volumes/COI/local_COI_db.*         492 MB, the sequences
    blast_volumes/COI/local_COI_db_marine.nal    180 bytes, pointing at:
    blast_volumes/COI/local_COI_db_marine.ids   12.8 MB, the identifiers

**A scope costs about 2.4% of the volume it restricts**, measured across the
four volumes of the marine library: 24.8 MB of aliases over 1,018 MB of
sequences. An earlier note here said 0.01%, from a 1 MB test database - that
measured only the `.nal` and missed the identifier list beside it, which is
where nearly all the size is and which grows with the number of sequences
kept. The real figure is still two orders of magnitude better than a copy.

Verified by reading the alias back rather than by trusting the exit code, per
`docs/protocols/reference-data.md` rule 5: `local_COI_db` reports 1,726,482
sequences and `local_COI_db_marine` reports 1,093,106.

One requirement has to be met first, and two of the four volumes in the
existing library do not meet it: a database can only be subset by identifier
if it was built with `-parse_seqids`. 12S and 18S were; 16S and COI were not,
and report their sequences as "BL_ORD_ID:0" with the real identifier stranded
in the title. `reindex` fixes that without re-downloading or re-parsing any
source, by reading the sequences back out of the volume and writing them
again with their identifiers restored.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from src.reference.library import ReferenceLibrary
from src.utils.platform import get_bundled_bin
from src.utils.platform import short_path
from src.utils.process import remove_quietly, run_tool
from src.utils.safety import refuse_if_protected
from src.utils.reporting import Reporter, console_reporter

#: What BLAST calls a sequence in a database built without -parse_seqids.
UNPARSED_ID = re.compile(r"^BL_ORD_ID:\d+$")

#: Suffix given to an alias, after the volume it restricts.
def alias_name(volume_name: str, scope: str) -> str:
    return f"{volume_name}_{scope}"


@dataclass
class Scope:
    """A named subset of a marker's references, defined by a catalogue query."""

    name: str
    description: str
    where: str                    # SQL, over reference_library
    parameters: tuple = ()

    def accessions(self, catalogue: sqlite3.Connection, marker: str) -> List[str]:
        """The identifiers this scope keeps, for one marker."""
        sql = (
            "SELECT accession FROM reference_library "
            f"WHERE marker_gene = ? AND ({self.where})"
        )
        return [row[0] for row in catalogue.execute(sql, (marker,) + self.parameters)]


#: Classes that are wholly terrestrial. Kept here as well as in build.py
#: because this filters a library that has already been built, where the
#: build-time filter can no longer help.
LAND_CLASSES = (
    "insecta", "collembola", "entognatha", "protura", "diplura",
    "arachnida", "diplopoda", "chilopoda", "amphibia", "lepidosauria",
)

#: The scopes TaxaTag knows how to build. More can be added without touching
#: anything else, because a scope is only a query and a name.
SCOPES: Dict[str, Scope] = {
    "marine": Scope(
        name="marine",
        description=(
            "Everything except the wholly terrestrial classes - insects, "
            "springtails, spiders, millipedes, centipedes, amphibians and "
            "squamates. Marine mammals, seabirds and every crustacean stay."
        ),
        where="LOWER(COALESCE(class,'')) NOT IN (%s)"
              % ",".join("?" * len(LAND_CLASSES)),
        parameters=LAND_CLASSES,
    ),
    "named": Scope(
        name="named",
        description=(
            "References identified at least to genus. Excludes records whose "
            "genus is a placeholder, which cannot contribute a name and can "
            "only dilute agreement between the references that can."
        ),
        where=(
            "COALESCE(genus,'') != '' "
            "AND genus NOT LIKE '%\\_X' ESCAPE '\\' "
            "AND genus NOT LIKE '%\\_XX' ESCAPE '\\' "
            "AND genus NOT LIKE '%\\_XXX' ESCAPE '\\' "
            "AND genus NOT LIKE '%\\_XXXX' ESCAPE '\\'"
        ),
    ),
}


def volume_is_subsettable(volume_dir: Path, volume_name: str) -> bool:
    """
    Whether a volume can have an alias made over it.

    A database built without `-parse_seqids` knows its sequences only by
    position, so there is no identifier for an alias to name.
    """
    blastdbcmd = get_bundled_bin("blastdbcmd")
    try:
        finished = subprocess.run(
            [str(blastdbcmd), "-db", volume_name, "-entry", "all", "-outfmt", "%a"],
            cwd=short_path(volume_dir), capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    first = (finished.stdout or "").strip().splitlines()[:1]
    return bool(first) and not UNPARSED_ID.match(first[0].strip())


def build_alias(
    volume_dir: Path,
    volume_name: str,
    scope: Scope,
    accessions: Iterable[str],
    reporter: Reporter,
) -> Optional[Path]:
    """
    Write an alias database restricting a volume to the given identifiers.

    Run from inside the volume's own folder with bare names, because BLAST
    splits a database argument on spaces and every real results folder on
    Windows has one in it somewhere.
    """
    refuse_if_protected(volume_dir, "write an alias into")

    accessions = list(accessions)
    if not accessions:
        reporter.warning(f"    {scope.name}: nothing matched, no alias written")
        return None

    listing = volume_dir / f"{alias_name(volume_name, scope.name)}.ids"
    listing.write_text("\n".join(accessions) + "\n", encoding="utf-8")

    result = run_tool(
        [
            str(get_bundled_bin("blastdb_aliastool")),
            "-db", volume_name,
            "-dbtype", "nucl",
            "-seqidlist", listing.name,
            "-out", alias_name(volume_name, scope.name),
            "-title", f"{volume_name} restricted to {scope.name}",
        ],
        cwd=short_path(volume_dir),
        reporter=reporter,
    )
    if not result.ok:
        reporter.error(f"    {scope.name}: {result.tail(2)}")
        return None
    return volume_dir / f"{alias_name(volume_name, scope.name)}.nal"


def reindex_volume(
    volume_dir: Path, volume_name: str, reporter: Reporter, keep: Optional[Set[str]] = None
) -> bool:
    """
    Rewrite a volume so its sequences carry their identifiers again.

    A volume built without `-parse_seqids` stores no identifiers, only the
    titles - so the information is not lost, merely in the wrong field. The
    sequences are read back out and written again with the title promoted to
    the identifier, which needs no source file and no download: the library is
    its own source.

    `keep` optionally restricts what is written, which is how a volume can be
    made smaller at the same time. Used for the springtails that a mis-spelled
    class name let into the COI library.
    """
    # Nothing rewrites a volume on a protected root. The reference drive
    # holds the original data, and is what made losing a library recoverable.
    refuse_if_protected(volume_dir, "rebuild the volume in")

    blastdbcmd = get_bundled_bin("blastdbcmd")
    makeblastdb = get_bundled_bin("makeblastdb")
    working = short_path(volume_dir)

    # A BLAST database cannot be renamed by renaming its files: the name is
    # written inside the metadata, and BLAST then looks for files that are no
    # longer there. Its own error message says so. The rebuild therefore
    # writes straight to the final name, and the safety net is the dumped
    # FASTA, which is kept beside the volume until the rebuild is confirmed
    # and can be rebuilt from by hand if anything goes wrong.
    dumped = volume_dir / f"{volume_name}_recovered.fasta"
    reporter.info(f"    reading {volume_name} back out")
    try:
        # Both fields are asked for because which one holds the identifier
        # depends on how the volume was built. Without -parse_seqids the
        # accession is "BL_ORD_ID:0" and the real name is stranded in the
        # title; with it, the accession is the name and the title is empty.
        # Reading only one of them silently recovers nothing.
        finished = subprocess.run(
            [str(blastdbcmd), "-db", volume_name, "-entry", "all",
             "-outfmt", "%a\t%t\t%s"],
            cwd=working, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=3600,
        )
        if finished.returncode != 0:
            reporter.error(f"    could not read it: {finished.stderr[:200]}")
            return False
        written = skipped = 0
        with open(dumped, "w", encoding="utf-8") as handle:
            for line in finished.stdout.splitlines():
                accession, _, rest = line.partition("\t")
                title, _, sequence = rest.partition("\t")
                accession = accession.strip()
                identifier = (
                    accession
                    if accession and not UNPARSED_ID.match(accession)
                    else (title.strip().split()[0] if title.strip() else "")
                )
                if not identifier or not sequence:
                    continue
                if keep is not None and identifier not in keep:
                    skipped += 1
                    continue
                handle.write(f">{identifier}\n{sequence}\n")
                written += 1
    except (OSError, subprocess.SubprocessError) as error:
        reporter.error(f"    could not read it: {error}")
        return False

    if not written:
        reporter.error("    nothing was recovered; leaving the volume alone")
        remove_quietly(dumped)
        return False
    reporter.info(
        f"    {written:,} sequences recovered"
        + (f", {skipped:,} left out" if skipped else "")
    )

    result = run_tool(
        [str(makeblastdb), "-in", dumped.name, "-dbtype", "nucl",
         "-parse_seqids", "-out", volume_name, "-title", volume_name],
        cwd=working, reporter=reporter, heartbeat="Rebuilding the volume",
    )
    if not result.ok:
        reporter.error(
            f"    rebuild failed: {result.tail(2)}\n"
            f"    the sequences are still in {dumped.name}, so nothing is lost"
        )
        return False

    if not volume_is_subsettable(volume_dir, volume_name):
        reporter.error(
            "    the rebuilt volume still has no identifiers; "
            f"the sequences are in {dumped.name}"
        )
        return False

    remove_quietly(dumped)
    return True


def scopes_for(library: ReferenceLibrary, marker: str) -> List[Path]:
    """Every alias already built over one marker's volume."""
    volume = library.volume(marker)
    return sorted(volume.path.parent.glob(volume.path.name + "_*.nal"))

# src/reference/licences.py
"""
What each reference source may be used for, and what a built library inherits.

A reference library is not one dataset. It is several, from projects with
different terms, combined into one file - and the combination is bound by the
strictest of them. That is easy to lose track of, because once the sequences
are in a BLAST volume they all look the same.

The specific thing this exists to prevent: BOLD's public sequence data is
CC-BY-NC-SA, and its **NC** clause forbids commercial use while its **SA**
clause requires any derived database to carry the same terms. A library
containing BOLD data may therefore be given away freely, with attribution,
and may not be sold - and a bespoke library built for sale that quietly
included BOLD would be non-compliant from the first copy, with no way to
recall what had already gone out.

Nothing here is a legal opinion. Each entry records what was checked, by whom
and when, so that an unverified claim is visible as one rather than passing
for a decision. `verified` being empty is not a small omission - it means
nobody has read the terms, and `describe_library` says so out loud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class Licence:
    """The terms one reference source is offered under."""

    name: str
    url: str
    #: May a copy be passed on at all, commercially or not?
    #:
    #: Separate from commercial_use because two sources here answer only this
    #: question, and answer it by silence. Under default copyright no licence
    #: means no permission, so a database publishing no terms cannot be
    #: redistributed even for free - a different and larger problem than a
    #: non-commercial clause, and worth its own field rather than being
    #: folded into one.
    redistribution: bool
    #: May a library containing this data be sold, or included in a paid
    #: product?
    commercial_use: bool
    #: Must a derived database carry these same terms? A "share alike" source
    #: makes the whole library it lands in freely redistributable, which is
    #: fine for a free library and fatal to a gated one.
    share_alike: bool
    #: The credit that must appear wherever the data is passed on.
    attribution: str
    #: Who established this and when. Empty means nobody has, and the entry
    #: is a guess rather than a finding.
    verified: str = ""
    note: str = ""

    @property
    def is_verified(self) -> bool:
        return bool(self.verified.strip())


#: The terms of every source a TaxaTag library can be built from.
#:
#: Keys match `sources.PARSERS`, so a build that can parse a source can also
#: say what it may be used for.
CHECKED = "Checked 8 September 2026"

SOURCE_LICENCES: Dict[str, Licence] = {
    "BOLD": Licence(
        name="CC-BY-NC-SA 4.0",
        url="https://creativecommons.org/licenses/by-nc-sa/4.0/",
        redistribution=True,
        commercial_use=False,
        share_alike=True,
        attribution=(
            "Barcode of Life Data System (BOLD), Centre for Biodiversity "
            "Genomics, University of Guelph."
        ),
        verified="Reported by the project owner, 8 September 2026",
        note=(
            "Public sequence records. The more permissive CC-BY that BOLD "
            "applied to most of its specimen *images* does not extend to "
            "these. The wording found was hedged - 'generally released "
            "under' - so the terms on the downloaded data package are worth "
            "reading before anything is sold. Access here was an "
            "institutional arrangement, which may carry conditions of its own."
        ),
    ),
    "MIDORI2": Licence(
        name="No terms published",
        url="https://www.reference-midori.info/",
        redistribution=False,
        commercial_use=False,
        share_alike=False,
        attribution="MIDORI2 (Leray et al. 2022), derived from NCBI GenBank.",
        verified=CHECKED + " - homepage and download page carry no licence",
        note=(
            "The site footer reads 'Copyright (c) MIDORI Reference 2. All "
            "rights reserved.' and the download page states nothing. That is "
            "not permission: under default copyright, no licence means no "
            "right to redistribute.\n"
            "\n"
            "The CC-BY-NC 4.0 that a search turns up belongs to the *paper* "
            "in Environmental DNA, not to the database. The two are routinely "
            "confused and are not the same thing.\n"
            "\n"
            "The underlying GenBank sequences are US government work and carry "
            "no copyright, so a library rebuilt from GenBank directly would be "
            "unencumbered. What is unclear is MIDORI2's curation and packaging "
            "on top. Asking them is the fix, and is a short email."
        ),
    ),
    "MitoFish": Licence(
        name="No terms published",
        url="https://mitofish.aori.u-tokyo.ac.jp/",
        redistribution=False,
        commercial_use=False,
        share_alike=False,
        attribution="MitoFish, Atmosphere and Ocean Research Institute, University of Tokyo.",
        verified=CHECKED + " - overview page read in a browser; no licence anywhere",
        note=(
            "Checked properly the second time. A plain fetch of the site returns "
            "almost nothing because the pages are rendered by JavaScript, and "
            "'I could not read it' is not 'it says nothing'. Rendered in a "
            "browser, the overview page contains no licence, no terms and no "
            "copyright line at all.\n"
            "\n"
            "It is not that they are unaware. The same page says that "
            "'sequences from other sources are not available for public access "
            "due to various licensing and copyright restrictions' - so they "
            "withhold what they believe they cannot share. That is a reason to "
            "think a request would be received well, and is not permission.\n"
            "\n"
            "**MitoFish draws on BOLD as well as GenBank**, taking BOLD records "
            "that are not identical to a GenBank one. So a MitoFish-derived "
            "volume may carry BOLD material, and BOLD's non-commercial clause "
            "with it, even where no record says 'BOLD'. The 12S volume is "
            "entirely MitoFish, so this is not hypothetical.\n"
            "\n"
            "The same trap as MIDORI2 besides: the CC-BY-NC that "
            "appears in search results is the licence of the MitoFish paper "
            "in Molecular Biology and Evolution. The tell is that it directs "
            "commercial enquiries to journals.permissions@oup.com - an Oxford "
            "University Press address, which is the journal and not the "
            "database.\n"
            "\n"
            "The download page states nothing at all. Asking them is the fix."
        ),
    ),
    "PR2": Licence(
        name="MIT",
        url="https://github.com/pr2database/pr2database/blob/master/LICENSE.md",
        redistribution=True,
        commercial_use=True,
        share_alike=False,
        attribution=(
            "PR2 (Protist Ribosomal Reference database). "
            "Copyright 2012-2021 D. Vaulot, L. Guillou, J. del Campo, "
            "F. Mahe and others."
        ),
        verified=CHECKED + " - read from LICENSE.md in the repository",
        note=(
            "Permissive: redistribution and commercial use are both allowed, "
            "provided the copyright notice and licence text travel with any "
            "copy. The only one of the four settled by reading the licence "
            "itself rather than by inference."
        ),
    ),
    "NCBI": Licence(
        name="Public domain (US government work)",
        url="https://www.ncbi.nlm.nih.gov/home/about/policies/",
        redistribution=True,
        commercial_use=True,
        share_alike=False,
        attribution="NCBI Taxonomy, National Center for Biotechnology Information.",
        verified="US government works carry no copyright; long established",
        note="The taxonomy every library uses to resolve names.",
    ),
}

#: What a library that names no sources at all is assumed to be. Unknown, and
#: therefore not sellable: silence is not permission.
UNKNOWN = Licence(
    name="Unknown",
    url="",
    redistribution=False,
    commercial_use=False,
    share_alike=False,
    attribution="",
    note="This library does not record where its data came from.",
)


def licence_for(source: str) -> Licence:
    """The terms one source is offered under, or UNKNOWN."""
    return SOURCE_LICENCES.get(source, UNKNOWN)


def may_be_shared(sources: Iterable[str]) -> Tuple[bool, List[str]]:
    """
    Whether a library built from these sources may be passed on at all.

    The prior question to `may_be_sold`, and the one that turned out to bite.
    A source that publishes no licence has not granted anything: under default
    copyright, silence is refusal, not permission. Two of the four sources
    here are in exactly that position, and between them they supply every
    12S and almost every 16S record - so this is not a corner case.

    It does not stop anybody *using* TaxaTag. A user downloads the sources
    themselves and builds their own library, which is what
    `src/reference/cli.py` has always been for. What it stops is handing that
    library on to somebody else.
    """
    reasons: List[str] = []
    for source in sorted(set(sources)):
        licence = licence_for(source)
        if source not in SOURCE_LICENCES:
            reasons.append(f"{source}: no licence recorded, so permission cannot be assumed")
        elif not licence.redistribution:
            reasons.append(f"{source}: {licence.name}, so no right to pass a copy on")
    return (not reasons), reasons


def may_be_sold(sources: Iterable[str]) -> Tuple[bool, List[str]]:
    """
    Whether a library built from these sources may be included in a paid
    product, and why not when it may not.

    Refuses on an unrecognised source as well as a non-commercial one. A
    source nobody has recorded terms for is not evidence of permission.
    """
    # Anything that cannot be given away certainly cannot be sold, so the
    # prior question is asked first and its reasons carried through.
    shareable, reasons = may_be_shared(sources)
    for source in sorted(set(sources)):
        licence = licence_for(source)
        if source not in SOURCE_LICENCES:
            continue
        if not licence.commercial_use and licence.redistribution:
            reasons.append(f"{source}: {licence.name} forbids commercial use")
        elif licence.share_alike:
            reasons.append(
                f"{source}: {licence.name} requires any derived library to "
                "carry the same terms, which cannot be reconciled with selling it"
            )
    return (not reasons), reasons


def unverified(sources: Iterable[str]) -> List[str]:
    """Sources whose terms nobody has actually read."""
    return sorted(
        source for source in set(sources)
        if not licence_for(source).is_verified
    )


def describe_library(sources: Iterable[str]) -> str:
    """
    The licence notice that ships beside a built library.

    Written for somebody who has downloaded a 2 GB folder and wants to know
    what they may do with it, so it leads with the answer rather than with a
    list of citations.
    """
    sources = sorted(set(sources))
    sellable, reasons = may_be_sold(sources)
    unchecked = unverified(sources)

    lines = [
        "TaxaTag reference library - terms of use",
        "=" * 40,
        "",
        "This library combines data from several projects. It is bound by the",
        "strictest of their terms, which are set out below.",
        "",
    ]

    shareable, share_reasons = may_be_shared(sources)
    if not shareable:
        lines += [
            "DO NOT PASS THIS LIBRARY ON.",
            "",
        ]
        lines += [f"  - {reason}" for reason in share_reasons]
        lines += [
            "",
            "No licence has been published for those sources, and silence is",
            "not permission. Using this library yourself is unaffected; what",
            "is missing is the right to give a copy to somebody else.",
            "",
            "Anyone who wants one can build their own: TaxaTag downloads",
            "nothing it cannot rebuild, and `python -m src.reference.cli",
            "recipe` prints exactly where each source comes from.",
            "",
        ]
    elif sellable:
        lines += [
            "MAY BE USED COMMERCIALLY, as far as the terms recorded here go.",
            "",
        ]
    else:
        lines += ["NOT FOR COMMERCIAL USE, because:", ""]
        lines += [f"  - {reason}" for reason in reasons]
        lines += [
            "",
            "Using it for research, teaching or any non-commercial purpose is",
            "unaffected. What is excluded is selling it, or including it in",
            "something sold.",
            "",
        ]

    lines += ["SOURCES AND ATTRIBUTION", ""]
    for source in sources:
        licence = licence_for(source)
        lines += [
            f"  {source} - {licence.name}",
            f"    {licence.attribution}",
        ]
        if licence.url:
            lines.append(f"    {licence.url}")
        if not licence.is_verified:
            lines.append("    NOT VERIFIED - nobody has read these terms.")
        lines.append("")

    if unchecked:
        lines += [
            "A WORD ON WHAT IS NOT KNOWN",
            "",
            "The terms for " + ", ".join(unchecked) + " have not been checked.",
            "They are recorded as best understood, not as established. Read them",
            "before relying on this notice for anything that matters.",
            "",
        ]

    lines += [
        "This notice describes the DATA. TaxaTag itself is separate software,",
        "free under the GNU General Public License version 3.",
    ]
    return "\n".join(lines)


#: The notice written into a library folder, and the machine-readable form
#: beside it. Two files because they have two readers: a person who has
#: downloaded a folder and wants to know what they may do with it, and the
#: program, which needs to answer the same question without parsing prose.
NOTICE_FILE = "LICENCE-DATA.txt"
TERMS_FILE = "licence.json"


def sources_in(catalogue_path) -> List[str]:
    """
    Which sources a built library actually drew on.

    Read from the catalogue rather than from the recipe that was meant to
    build it, because what matters is what is in the file. A recipe describes
    an intention; a library that was built in stages, or had a marker added
    later, may not match it.

    Asked one source at a time, which is the opposite of the obvious way
    round and the reason this is quick. The build records provenance as
    "BOLD ID: ..." or "MitoFish Acc: ...", so `common_name` is very nearly
    unique - 2.9 million distinct values in 2.98 million rows on the marine
    core. `SELECT DISTINCT common_name` therefore builds a temporary index
    of almost every row in the library and hands the lot back to be examined
    one string at a time, to recover a set with four members in it. That took
    37 seconds, inside `Check my setup`, where it is a frozen window.

    Probing instead - does *this* source appear? - stops at the first
    matching row and costs 2 seconds. The work now scales with the number of
    sources rather than the size of the library, which is the right way
    round: the first is a short fixed list and the second is not.
    """
    import sqlite3
    from pathlib import Path

    path = Path(catalogue_path)
    if not path.exists():
        return []
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        found = set()
        for source in SOURCE_LICENCES:
            # The source is the first alphanumeric word, so a name matches
            # when it is exactly the source or the source followed by
            # something that cannot be part of the same word. GLOB rather
            # than LIKE because it is case-sensitive and takes a character
            # class, and "PR2" must not be answered by a "PR2X" record.
            row = connection.execute(
                "SELECT 1 FROM reference_library "
                "WHERE common_name = ? OR common_name GLOB ? LIMIT 1",
                (source, source + "[^A-Za-z0-9]*"),
            ).fetchone()
            if row:
                found.add(source)
        connection.close()
    except sqlite3.Error:
        return []
    # Every library resolves its names through NCBI taxonomy, whether or not
    # a record says so.
    found.add("NCBI")
    return sorted(found)


def write_notice(library_root, sources: Optional[Iterable[str]] = None) -> List[str]:
    """
    Put the terms beside the data, and report what was written.

    Beside the data on purpose. A library is copied between machines, shared
    on a memory stick and downloaded years after anybody read a web page, and
    a licence that lives anywhere else does not make those journeys.
    """
    import json
    from pathlib import Path

    root = Path(library_root)
    if sources is None:
        sources = sources_in(root / "taxatag_reference_core.db")
    sources = sorted(set(sources))

    sellable, reasons = may_be_sold(sources)
    (root / NOTICE_FILE).write_text(describe_library(sources) + "\n", encoding="utf-8")
    (root / TERMS_FILE).write_text(
        json.dumps(
            {
                "sources": sources,
                "commercial_use": sellable,
                "reasons_against_commercial_use": reasons,
                "unverified": unverified(sources),
                "licences": {
                    source: {
                        "name": licence_for(source).name,
                        "url": licence_for(source).url,
                        "attribution": licence_for(source).attribution,
                        "commercial_use": licence_for(source).commercial_use,
                        "share_alike": licence_for(source).share_alike,
                        "verified": licence_for(source).verified,
                    }
                    for source in sources
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return [NOTICE_FILE, TERMS_FILE]

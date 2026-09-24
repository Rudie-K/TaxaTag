# src/reference/cli.py
"""
Building and inspecting a reference library from the command line.

    python -m src.reference.cli info      --library <folder>
    python -m src.reference.cli add       --library <folder> --marker 16S --source ...
    python -m src.reference.cli recipe    --library <folder> --marker 16S
    python -m src.reference.cli create    --library <folder>

Building a library is an occasional, long-running job with large downloads
behind it, so it lives here rather than in the analysis interface: it is not
something an ecologist should be able to start by accident in the middle of a
run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

from src.reference import licences
from src.reference import markers as markers_module
from src.reference.build import (
    SourceSpec,
    add_marker,
    create_empty_library,
    ensure_indexes,
    load_accession_taxids,
    rebuild_taxonomy_matrix,
    record_genes,
    repair_bundled_names,
)
from src.reference.library import ReferenceLibrary
from src.reference.sources import MITOGENOME_MIN_LENGTH
from src.utils.reporting import console_reporter

#: Known recipes: for each marker, the sources to build it from and the rules
#: to apply. Filenames are relative to the library folder, so a user who has
#: downloaded the archives into it can build with a single command.
#:
#: Order matters. Earlier sources take precedence, so a curated source acts as
#: the authority for the taxa it covers and a broader one fills in the rest.
RECIPES: Dict[str, dict] = {
    "12S": {
        "description": "Fish 12S, for MiFish and similar primer sets.",
        "sources": [
            {
                "file": "mitofishdb.fa.gz",
                "source": "MitoFish",
                "taxid_map": "seq_taxonid.parquet",
                "note": "Expert-curated fish 12S; the authority for fish.",
            },
            {
                "file": "MIDORI2_UNIQ_NUC_GB272_srRNA.fasta.zip",
                "source": "MIDORI2",
                "note": "Fills in non-fish vertebrates that MitoFish excludes.",
            },
        ],
        "require_family": True,
    },
    "16S": {
        "description": "Vertebrate 16S, for MarVer3 and similar primer sets.",
        "sources": [
            {
                "file": "MIDORI2_UNIQ_NUC_GB272_lrRNA.fasta.zip",
                "source": "MIDORI2",
                "note": "Curated 16S from GenBank, covering vertebrates and invertebrates.",
            },
            {
                "file": "mitofishdb.fa.gz",
                "source": "MitoFish",
                "taxid_map": "seq_taxonid.parquet",
                "min_length": MITOGENOME_MIN_LENGTH,
                "note": (
                    "Fish backup: whole mitochondrial genomes, which contain the "
                    "16S region even though MitoFish does not label it."
                ),
            },
        ],
        # Added after finding the first build had none: 42% of the resulting
        # library was terrestrial, and 30,188 records were fungi, land plants
        # and velvet worms, which a vertebrate 16S primer set cannot amplify.
        "marine_only": True,
        "require_family": True,
    },
    "COI": {
        "description": "Animal COI, for Leray, Folmer and similar primer sets.",
        "sources": [
            {
                "file": "MIDORI2_UNIQ_NUC_GB272_CO1_BLAST.fasta.zip",
                "source": "MIDORI2",
                "note": "Curated COI from GenBank, with NCBI taxonomy already applied.",
            },
            {
                "file": "BOLD_Public.30-Jun-2026.fasta.gz",
                "source": "BOLD",
                "note": "The global barcode library, for taxa GenBank does not hold.",
            },
        ],
        "marine_only": True,
        "require_family": True,
    },
    "18S": {
        "description": "Eukaryote 18S, for plankton, algae and protists.",
        "sources": [
            {
                "file": "pr2_version_5.1.1_SSU_taxo_long.fasta.gz",
                "source": "PR2",
                "note": "The standard curated 18S reference for micro-eukaryotes.",
            },
        ],
        "require_family": True,
    },
}

#: Where the source archives come from, printed by the `recipe` command so a
#: user can fetch what they are missing.
DOWNLOADS = {
    "MIDORI2": (
        "https://www.reference-midori.info/download.php  -> Databases -> "
        "<newest GenBank release> -> BLAST -> uniq -> fasta. "
        "Take srRNA for 12S, lrRNA for 16S, CO1 for COI."
    ),
    "MitoFish": "https://mitofish.aori.u-tokyo.ac.jp/  -> Download -> complete + partial",
    "PR2": "https://github.com/pr2database/pr2database/releases  -> SSU taxo_long fasta",
    "BOLD": "https://bench.boldsystems.org/  -> Data packages -> BOLD_Public FASTA",
}


def _specs_from_recipe(library: Path, marker: str, reporter) -> List[SourceSpec]:
    """Turn a recipe into source specifications, skipping what is not present."""
    recipe = RECIPES[marker]
    specs: List[SourceSpec] = []
    for entry in recipe["sources"]:
        path = library / entry["file"]
        if not path.exists():
            reporter.warning(f"Not present, skipping: {entry['file']}")
            continue
        taxid_map = None
        if entry.get("taxid_map"):
            taxid_map = load_accession_taxids(library / entry["taxid_map"], reporter)
        specs.append(
            SourceSpec(
                path=path,
                source=entry["source"],
                min_length=entry.get("min_length", 0),
                max_length=entry.get("max_length", 0),
                note=entry.get("note", ""),
                taxid_map=taxid_map,
            )
        )
    return specs


def command_info(args, reporter) -> int:
    library = ReferenceLibrary(Path(args.library))
    if not library.exists:
        print(f"No reference library at {args.library}", file=sys.stderr)
        return 1

    counts = library.counts_by_marker()
    available = library.available_markers()

    print(f"\nReference library: {library.root}")
    print(f"Catalogue:         {library.catalogue_path.name}\n")
    print(f"{'Marker':8s} {'Sequences':>14s}  {'Searchable':10s}  Description")
    print("-" * 78)
    for marker in sorted(set(counts) | set(available) | set(markers_module.known_markers())):
        n = counts.get(marker, 0)
        searchable = "yes" if marker in available else "no"
        description = markers_module.describe(marker) if marker in markers_module.MARKERS else ""
        print(f"{marker:8s} {n:>14,}  {searchable:10s}  {description[:40]}")
    print("-" * 78)
    print(f"{'total':8s} {sum(counts.values()):>14,}\n")
    library.close()
    return 0


def command_index(args, reporter) -> int:
    try:
        ensure_indexes(Path(args.library), reporter)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def command_repair(args, reporter) -> int:
    taxids = load_accession_taxids(Path(args.taxids), reporter)
    if not taxids:
        print("the accession-to-taxid table is empty or missing", file=sys.stderr)
        return 1
    try:
        repair_bundled_names(Path(args.library), taxids, args.marker, reporter)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def command_genes(args, reporter) -> int:
    from src.reference.genes import load_mitofish

    for path in (args.annotation, args.descriptions):
        if not Path(path).exists():
            print(f"not found: {path}", file=sys.stderr)
            return 1
    reporter.info("Reading MitoFish's gene annotation and GenBank titles...")
    annotated, titles = load_mitofish(Path(args.annotation), Path(args.descriptions))
    try:
        record_genes(Path(args.library), annotated, titles, reporter)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def command_taxonomy(args, reporter) -> int:
    if not Path(args.taxdump).exists():
        print(f"not found: {args.taxdump}", file=sys.stderr)
        return 1
    try:
        rebuild_taxonomy_matrix(Path(args.library), Path(args.taxdump), reporter)
    except (FileNotFoundError, KeyError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def command_create(args, reporter) -> int:
    create_empty_library(Path(args.library), reporter)
    print(
        "\nThe library is empty. Add NCBI taxonomy first, then markers:\n"
        "  python -m src.reference.cli recipe --library <folder> --marker 16S\n"
    )
    return 0


def command_recipe(args, reporter) -> int:
    marker = args.marker
    recipe = RECIPES.get(marker)
    if recipe is None:
        print(f"No recipe for {marker}. Known: {', '.join(RECIPES)}", file=sys.stderr)
        return 2

    library = Path(args.library)
    print(f"\n{marker}: {recipe['description']}\n")
    missing = []
    for entry in recipe["sources"]:
        path = library / entry["file"]
        state = "present" if path.exists() else "MISSING"
        print(f"  [{state:7s}] {entry['file']}")
        print(f"              {entry.get('note', '')}")
        if not path.exists():
            missing.append(entry["source"])
    if recipe.get("marine_only"):
        print("\n  Records outside marine phyla will be left out.")
    if recipe.get("require_family"):
        print("  Records not identified at least to family will be left out.")

    # What may be done with the result, said before it is built rather than
    # discovered afterwards. A library takes hours to make and its terms are
    # not visible anywhere in the finished files.
    sources = [entry["source"] for entry in recipe["sources"]] + ["NCBI"]
    print()
    print("  Sources: " + ", ".join(dict.fromkeys(sources)))
    sellable, reasons = licences.may_be_sold(sources)
    print("  Commercial use: " + ("permitted" if sellable else "NOT permitted"))
    for reason in reasons:
        print(f"      {reason}")
    unchecked = licences.unverified(sources)
    if unchecked:
        print("  Terms not verified for: " + ", ".join(unchecked))

    if getattr(args, "for_sale", False) and not sellable:
        # Refused, not warned. The flag means somebody has said this library
        # is going into a paid product, and a warning at that moment is a line
        # of output nobody reads before a four-hour build.
        print()
        print("Refusing to build: --for-sale was given, and these sources do not")
        print("permit it. Build without --for-sale for a freely distributable")
        print("library, or use sources that allow commercial use.")
        return 1

    if missing:
        print("\nDownload what is missing from:")
        for source in dict.fromkeys(missing):
            print(f"  {source:10s} {DOWNLOADS.get(source, 'see the source website')}")
        print(f"\nPut the file in {library} and run this command again.")
        return 1

    if not args.build:
        print("\nEverything is present. Add --build to build the marker.")
        return 0

    specs = _specs_from_recipe(library, marker, reporter)
    result = add_marker(
        library_root=library,
        marker=marker,
        source_files=specs,
        reporter=reporter,
        marine_only=recipe.get("marine_only", False),
        require_family=recipe.get("require_family", False),
    )
    return 0 if result.volume else 1


def command_add(args, reporter) -> int:
    specs = []
    for item in args.source:
        # --source <file>:<source name>[:<min length>]
        parts = item.split(":")
        if len(parts) < 2:
            print(f"Expected <file>:<source>, got '{item}'", file=sys.stderr)
            return 2
        specs.append(
            SourceSpec(
                path=Path(parts[0]),
                source=parts[1],
                min_length=int(parts[2]) if len(parts) > 2 and parts[2] else 0,
            )
        )
    result = add_marker(
        library_root=Path(args.library),
        marker=args.marker,
        source_files=specs,
        reporter=reporter,
        marine_only=args.marine_only,
        require_family=not args.keep_incomplete,
    )
    return 0 if result.volume else 1


def command_scope(args, reporter) -> int:
    """
    Build, or list, the alias databases that restrict a volume to a subset.

    This is the source-side half of decision 0006. An alias costs a fraction
    of a percent of the data it restricts and takes seconds to make, so a
    scope is worth having wherever a run would otherwise search references it
    can never want. Nothing in the shipped application can build one: the
    application only ever reads a library.
    """
    from src.reference import scopes as scopes_module

    library = ReferenceLibrary(args.library)
    if not library.exists:
        reporter.error(f"No reference library at {args.library}")
        return 1

    markers = [args.marker] if args.marker else library.available_markers()

    if args.list:
        for marker in markers:
            built = library.scopes(marker)
            reporter.info(f"{marker}: " + (", ".join(built) if built else "no scopes built"))
        reporter.info("")
        reporter.info("Scopes that can be built:")
        for name, scope in sorted(scopes_module.SCOPES.items()):
            reporter.info(f"  {name} - {scope.description}")
        return 0

    scope = scopes_module.SCOPES.get(args.name)
    if scope is None:
        reporter.error(
            f"No scope called '{args.name}'. Known: "
            + ", ".join(sorted(scopes_module.SCOPES))
        )
        return 1

    catalogue = library.connect()
    failures = 0
    for marker in markers:
        volume = library.volume(marker)
        if not volume.exists:
            reporter.warning(f"{marker}: no volume, skipped")
            continue
        if not scopes_module.volume_is_subsettable(volume.path.parent, volume.path.name):
            # Built without -parse_seqids, so its sequences have no identifier
            # for an alias to name. Fixable with a reindex, which is a separate
            # and far more destructive operation - see the reference-data
            # protocol - so it is reported rather than done here.
            reporter.error(
                f"{marker}: this volume cannot be subset. It was built without "
                "-parse_seqids, so rebuild it before making scopes over it."
            )
            failures += 1
            continue

        accessions = scope.accessions(catalogue, marker)
        reporter.info(f"{marker}: {len(accessions):,} of its references are '{scope.name}'")
        written = scopes_module.build_alias(
            volume.path.parent, volume.path.name, scope, accessions, reporter
        )
        if written is None:
            failures += 1
        else:
            reporter.success(f"{marker}: wrote {written.name}")
    library.close()
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taxatag-reference",
        description="Build and inspect a TaxaTag reference library.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--library", "-l", required=True, type=Path,
        help="the reference library folder",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    info = subparsers.add_parser("info", help="show what the library contains")
    info.set_defaults(handler=command_info)

    create = subparsers.add_parser("create", help="create an empty library")
    create.set_defaults(handler=command_create)

    index = subparsers.add_parser("index", help="add the catalogue indexes a newer TaxaTag expects to an existing library")
    index.set_defaults(handler=command_index)

    repair = subparsers.add_parser("repair", help="name the records a source's bundled accessions left nameless (decision 0028)")
    repair.add_argument("--taxids", required=True, help="the source's accession-to-taxid Parquet table (MitoFish: seq_taxonid.parquet)")
    repair.add_argument("--marker", "-m", default="12S")
    repair.set_defaults(handler=command_repair)
    genes = subparsers.add_parser("genes", help="record which gene each record holds, from its source's annotation (decision 0041)")
    genes.add_argument("--annotation", required=True, help="MitoFish's seq_annotation.parquet")
    genes.add_argument("--descriptions", required=True, help="MitoFish's seq_description.parquet")
    genes.set_defaults(handler=command_genes)
    taxonomy = subparsers.add_parser("taxonomy", help="rebuild the taxonomy matrix from NCBI's ranked lineage (decision 0042)")
    taxonomy.add_argument("--taxdump", required=True, help="NCBI's new_taxdump.tar.gz")
    taxonomy.set_defaults(handler=command_taxonomy)

    recipe = subparsers.add_parser(
        "recipe", help="check, and optionally build, a marker from known sources"
    )
    recipe.add_argument("--marker", "-m", required=True, choices=sorted(RECIPES))
    recipe.add_argument("--build", action="store_true", help="build it, not just check")
    recipe.add_argument(
        "--for-sale", action="store_true",
        help=(
            "this library is for a paid product: refuse to build it if any "
            "source forbids commercial use"
        ),
    )
    recipe.set_defaults(handler=command_recipe)

    add = subparsers.add_parser("add", help="build a marker from files you name")
    add.add_argument("--marker", "-m", required=True)
    add.add_argument(
        "--source", action="append", required=True, metavar="FILE:SOURCE[:MINLEN]",
        help=f"a source file and its format ({', '.join(DOWNLOADS)})",
    )
    add.add_argument("--marine-only", action="store_true")
    add.add_argument(
        "--keep-incomplete", action="store_true",
        help="keep references not identified as far as family",
    )
    add.set_defaults(handler=command_add)

    scope = subparsers.add_parser(
        "scope",
        help="build or list the subsets a run can be restricted to",
        description=(
            "A scope is an alias database naming part of a marker's volume. "
            "Searching it searches only that subset, and it costs a fraction "
            "of a percent of the data it restricts rather than a second copy. "
            "Set 'reference_scope' in a settings file to use one."
        ),
    )
    scope.add_argument(
        "--name", "-n", default="marine",
        help="which scope to build (default: marine)",
    )
    scope.add_argument(
        "--marker", "-m", help="just this marker (default: every marker present)",
    )
    scope.add_argument(
        "--list", action="store_true",
        help="show what is already built and what could be, and build nothing",
    )
    scope.set_defaults(handler=command_scope)

    return parser


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, console_reporter())


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    raise SystemExit(main())

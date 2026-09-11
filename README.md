# TaxaTag

**Environmental DNA to species, without the command line.**

TaxaTag turns the sequencing files from an eDNA metabarcoding experiment into a
table of which species were found in which sample. Designed on Windows and coming to MacOS and Linux soon, TaxaTag is built for ecologists and geneticists who would rather avoid R and the Command Line while maintaining the same transparency and data quality.

![The TaxaTag window](resources/screenshot-run.png)

Metabarcoding analysis has generally meant a chain of command-line tools, a
Linux machine, and paths and thresholds edited into scripts by hand. TaxaTag
does the same science through a window, with every setting visible and
changeable, and with a reference library that identifies a whole survey
offline in seconds.

---

## What you would use it for

- Monitoring for **invasive species** in rivers, lakes or coastal water
- Confirming whether a **particular species is present** at a site
- Describing the **community** in a set of water or sediment samples
- **Before-and-after** surveys around a development, a restoration or a spill
- Teaching metabarcoding without giving a class a Linux terminal first

TaxaTag ships primers for the four markers most surveys use — **12S** and
**16S** for vertebrates, **COI** for invertebrates, **18S** for eukaryotic
plankton — and eleven published primer sets between them. You can add your own.

It is most useful where good reference sequences already exist for your
markers. Where they do not, TaxaTag will tell you it cannot name something
rather than guessing.

---

## What is different about it

| | |
|---|---|
| **Nothing is hidden** | Every threshold and path is in the window and in a settings file. If a reviewer asks why a sequence was kept, the answer is visible. |
| **It says when it does not know** | Where equally good references disagree, TaxaTag reports the rank they agree on — genus, or family — instead of picking the first hit and calling it a species. |
| **Each marker searches its own references** | A 12S read is never compared against COI, which removes a whole class of spurious match. |
| **It checks before it starts** | Inputs, tools and settings are all validated first. Failing in the first second beats failing forty minutes in. |
| **It works offline** | A local library identifies a survey in under a second, with no network and no dependence on a service staying up. |
| **It can prove it works** | A bundled self-test runs four samples with known answers, so you can confirm the installation is sound before trusting it with real data. |
| **It tells you about updates, and takes no for an answer** | If a newer version exists, TaxaTag says so once and offers to install it. "Don't ask again" applies to that version, not to updates forever. It never updates itself without being asked. |

Each of these was a decision with trade-offs, and the reasoning is recorded
in the project's own notes rather than here.

---

## Getting started

### 1. Download it

Everything is on the [releases page](https://github.com/Rudie-K/TaxaTag/releases).

| | | |
|---|---|---|
| **Installer** | `TaxaTag-<version>-Windows-x64-Setup.exe` | Installs for you. No administrator password needed. |
| **Portable** | `TaxaTag-<version>-Windows-x64-portable.zip` | Unzip and run. Nothing is installed, nothing is written outside the folder. |

Take the **portable zip** if you are on a managed university or consultancy
laptop, or if you are unsure. It is the same program.

**On macOS and Linux** there is no packaged download yet. TaxaTag itself runs
on both — the tools it needs are bundled for all three systems — so you can
run it from the source folder today with `./taxatag.sh`, which sets itself up
on the first run. A packaged build for each is the next thing planned.

### 2. Check it works

Open TaxaTag and press **Test the analysis pipeline**.

It analyses four bundled samples whose answers are known and tells you whether
it got them right. It needs no data of yours and no reference library, and it
takes under a minute. This is the thing to run first on a machine where
nothing is set up yet — if it passes, the installation is sound.

### 3. Get a reference library

TaxaTag offers to download the **marine core** library the first time it
starts without one. It is 653 MB to download and about 2 GB installed, and it
covers 12S, 16S, COI and 18S.

You do not have to take it. Without a library TaxaTag searches NCBI over the
web instead, which works and is slower. The offer stays in the library list on
the *Run* tab if you would rather decide later.

### 4. Run your data

Point **Sequencing files** at the folder holding your reads — paired files are
matched up automatically, whatever they are named, and files split across lanes
are joined back together. Choose a **Results folder**. Press **Check my setup**
to catch problems before anything runs, then **Start analysis**.

### 5. Read the results

Each run creates its own dated folder, so an earlier run is never overwritten.
The file most people want is `05_results/species_composition.csv`: one row per
species, one column per sample, with the rank resolved and the
evidence behind it.

Every setting has its own explanation in the window, so the program is the
manual for the rest: what each results file contains, how to resume an
interrupted run, and what to do when something looks wrong.

---

## Keeping it up to date

When a newer version exists, TaxaTag says so the next time it starts, and
offers three answers: install it now, be reminded next time, or skip this
version. **Skipping applies to the version on offer**, not to updates in
general — you will still hear about the one after it.

Choosing to update closes TaxaTag and runs the installer, which replaces the
existing installation rather than adding a second one. Your settings, results
and reference libraries are not touched.

Nothing is ever installed without being asked, and nothing is installed that
cannot be checked against the checksum published with it. If there is no
internet connection, TaxaTag says nothing at all rather than complaining
about a check you did not ask for.

Reference libraries are offered the same way when a newer one is published,
and are replaced without closing anything.

---

## The reference library

A reference library is what your sequences are compared against. TaxaTag uses
one BLAST volume per marker plus a catalogue giving the lineage of every
record, which is why a 12S read is only ever searched against 12S references.

The **marine core** covers fish, invertebrates and eukaryotic plankton across
all four markers — around 3 million sequences, built from MitoFish, MIDORI2,
BOLD, PR2 and the NCBI taxonomy. It is free, and TaxaTag can download it for
you.

You can also build your own, extend the core with your own sequences, or point
TaxaTag at an existing BLAST database. The *Run* tab's library list is where
all of that is chosen.

---

## Help

Every setting in TaxaTag explains itself in the window, under the field it
belongs to. **Check my setup** names anything that would stop a run before it
starts, and **Test the analysis pipeline** proves the installation works on
data whose answer is already known.

Beyond that, this page is the manual. **Help → About** shows the version,
the licence and where your settings file lives — worth having to hand if you
get in touch.

---

## Licence

TaxaTag is free software under the **GNU General Public License, version 3**.
The full text is in [LICENSE](LICENSE).

You may use it for anything, including commercial work, and you may change and
redistribute it. The condition is that anyone you pass a copy to gets the same
freedoms and the source to go with it.

The bundled tools carry their own terms: VSEARCH is GPL-3/BSD,
Cutadapt is MIT, and NCBI BLAST+ and the SRA Toolkit are United States public
domain (correct as of 09/09/2026).

**The name and the logo are separate.** "TaxaTag" and its logo are not
covered by the GPL, and all rights in them are reserved — a provision the GPL itself allows, in section 7(e). Use
the name and show the logo whenever you are talking *about* TaxaTag: cite it,
link it, teach with it, put it on a slide. What the carve-out prevents is a
*modified* version going out under this name and mark. TaxaTag puts species
names in people's papers, and somebody who cannot tell a fork's answer from
this program's has been misled twice — by the result, and by the name that
told them to trust it. Fork freely; rename when you do.
[Full terms](resources/BRANDING.md).

**Reference data is separate.** A reference library is a dataset, not part of
this program, and the sources it is built from — MitoFish, MIDORI2, PR2, BOLD
and the NCBI taxonomy — each carry their own terms. Every library TaxaTag
builds or installs carries a `LICENCE-DATA.txt` saying what its contents
permit. Check it before passing a library on.

---

## Citing TaxaTag

If you use TaxaTag in published work, please cite it and the tools it is built
on.

> Rudie Kauhanen, Lucy Thomas and Ruth Farrant. **TaxaTag: a localised open-source eDNA metabarcoding pipeline for non-bioinformaticians**, 2026.

Please also cite:

- **Cutadapt** — Martin, M. (2011). *EMBnet.journal* 17(1), 10–12.
- **VSEARCH** — Rognes, T., Flouri, T., Nichols, B., Quince, C., Mahé, F.
  (2016). *PeerJ* 4:e2584.
- **BLAST** — Camacho, C. *et al.* (2009). *BMC Bioinformatics* 10:421.
- **NCBI Taxonomy** — Schoch, C.L. *et al.* (2020). *Database* 2020:baaa062.

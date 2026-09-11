# The TaxaTag name and logo

**© 2026 Rudie Kauhanen, Lucy Thomas and Ruth Farrant. All rights reserved.**

The code in this repository is free software under the GPL-3.0. The name
"TaxaTag" and the TaxaTag logo are not. No licence is granted to them, and
the paragraph headed "What you may do" below is a permission rather than a
licence — it can be relied on, and it is deliberately narrow.

This is not a contradiction and not an afterthought bolted onto a free
licence. GPL-3 section 7(e) explicitly permits an additional term "declining
to grant rights under trademark law for use of some trade names, trademarks,
or service marks". Copyright and trademark are separate systems: the GPL
governs the first and says nothing about the second.

---

## What this covers

- The name **TaxaTag**
- The logo, in every form: `taxatag_logo_cream_background.jpeg`,
  `taxatag_logo_colour_background.jpeg`, `taxatag.png`, `taxatag.ico`,
  `taxatag_emblem.png`, and anything derived from them by
  `resources/make_icon.py`
- `screenshot-run.png`, which is a picture of the branded interface

Everything else in this repository is GPL-3.0, including `make_icon.py`
itself.

---

## What you may do

**Refer to TaxaTag by name, and show the logo or a screenshot of the
program, when what you are talking about is TaxaTag.** Cite it in a paper,
name it in a methods section, link to it, put it on a slide, write a review
of it, teach with it, file a bug report about it. No permission needs to be
sought for any of that, and none should be.

**Use, study, change and redistribute the code**, exactly as the GPL says.
That right is not diminished by anything on this page.

---

## What you may not do

**Release a modified version under the name TaxaTag, or carrying its logo.**

The GPL gives you the right to change the program and pass it on. It does not
give you the right to have your version mistaken for this one. If you
distribute a fork, give it your own name and your own mark.

The reason is not ownership for its own sake. TaxaTag identifies species from
environmental DNA, and people put those identifications in papers and
reports. Somebody who downloads a modified TaxaTag, gets a wrong answer from
it, and has no way to tell it apart from the original has been misled twice
over — once by the result, and once by the name that told them to trust it.

---

## Who owns what

The name and the logo are held jointly by **Rudie Kauhanen, Lucy Thomas and
Ruth Farrant**, who contributed the elements they are made of. All rights in
them are reserved.

The code is copyright the same three and licensed to everybody under the
GPL-3.0. The two statements sit side by side without conflicting, because
they are about different things.

If you want to use the name or the logo in a way this page does not clearly
allow, ask first — contact the project rather than assuming.

---

## If you fork this project

Nothing here is meant to make forking hard, and the practical steps are
short:

1. Rename the program, in `src/version.py` (`APP_NAME`) and in the installer
   script.
2. Replace or delete the files listed above.
3. Change `AppId` in `installer/taxatag.iss` to a new GUID of your own, so
   your installer does not overwrite somebody's TaxaTag installation.

That is the whole of it. The science, the pipeline and every line of the code
are yours to take.

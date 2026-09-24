"""
The version of TaxaTag, in one place.

It was in three: the run manifest, the macOS bundle, and the installer
script. Three copies drift, and the way this one would have drifted is the
expensive way round - `installer/build_installer.py` reads the installer
script, so bumping that alone produces a release labelled 1.1.0 whose every
result manifest says 1.0.0.

That matters more here than in most programs. The manifest is the record of
how a result was produced, and it is what somebody consults when a number
in a paper is questioned two years later. A version that names the wrong
program makes that record worse than useless, because it is confidently
wrong rather than absent.

The installer script cannot import this - Inno Setup is not Python - so it
keeps its own copy and a test asserts the two agree. That is the honest
arrangement: one source of truth, and a check on the one place that cannot
read it.
"""

from __future__ import annotations

__version__ = "1.1.0"

#: The permanent identity of the installed application on Windows.
#:
#: Inno Setup uses this as the uninstall registry key, and it is how an
#: update recognises the copy it is replacing. **It must never change.** A
#: new value makes the next version install alongside the old one rather
#: than over it, leaving two entries in Add/Remove Programs and two folders,
#: on every machine that ever had the previous release.
#:
#: The first value written here was not a valid GUID - it read
#: "...-TAXATAG00001", which contains letters that are not hexadecimal. Inno
#: Setup accepts any string, so it would have compiled and shipped, and the
#: mistake would have been permanent the moment somebody installed it.
APP_ID = "81659C9A-9140-41E7-9F73-50DAB247E428"

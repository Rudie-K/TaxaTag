# src/analysis/__init__.py
"""
What is said about a finished run, after the pipeline has said what it
found.

Everything here reads a run folder and writes tables into its
`06_analysis/` folder. Nothing here imports Qt, launches a tool or
touches the reference data as anything but a catalogue to ask questions
of - so the terminal and the window get the same numbers, and the tests
run without a display. Rule 1 of `docs/scopes.md` applies exactly as it
does to a pipeline stage.

Why this is a package and not part of the pipeline: a run is finished
when the species table is written, and that table is the record. Analysis
is a second, repeatable act on the record - it may be run again with a
different species list or a longer candidate list, and it never changes
what the run found.
"""

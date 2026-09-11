# src/gui/settings_file.py
"""
Reading and editing a settings file without opening a text editor.

The Settings tab holds the choices an ordinary run needs. A settings file
holds more than that - search limits, tool locations, the batch size used when
talking to NCBI - and until now the only way to see or change those was to
open the YAML in a text editor and get the indentation right. For the people
this program is written for, that is the same as not being able to change them
at all.

So this shows the whole file, every setting labelled and explained, grouped the
way someone would look for them. It is the same file either way: what is shown
is loaded through PipelineConfig, so a file missing a setting shows the value
that would actually be used rather than a blank, and saving writes a complete
file back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.utils import paths
from typing import Any, Dict, List, Optional, Tuple

import yaml
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.gui.widgets import HelpLabel, PathPicker
from src.pipeline.config import PipelineConfig


@dataclass
class Field:
    """One setting, and how it should be shown."""

    path: str            # where it lives in the file, e.g. "runtime.threads"
    label: str
    kind: str            # folder, file, text, int, float, percent, bool, choice
    help: str = ""
    minimum: float = 0.0
    maximum: float = 1_000_000.0
    decimals: int = 2
    choices: List[Tuple[str, Any]] = field(default_factory=list)


#: Every setting in the file, grouped the way someone would look for it.
#:
#: Ordered by how often it is touched rather than by where it sits in the file:
#: folders first, because they change every project, and tool locations last,
#: because almost nobody should ever need them.
GROUPS: List[Tuple[str, str, List[Field]]] = [
    (
        "Where things are",
        "The folders this project reads from and writes to.",
        [
            Field("paths.input_dir", "Sequencing files", "folder",
                  "The folder holding your reads."),
            Field("paths.output_base", "Results folder", "folder",
                  "Each run creates its own dated folder inside this one."),
            Field("paths.reference_dir", "Reference library", "folder",
                  "A TaxaTag reference library. Leave empty to search NCBI instead."),
            Field("paths.blast_db", "Another BLAST database", "file",
                  "Only needed when searching a database you built yourself."),
            Field("paths.taxonomy_cache", "Taxonomy cache", "file",
                  "Lineages already looked up, so they are not fetched twice."),
        ],
    ),
    (
        "Reading the sequence files",
        "How raw files are turned into something the pipeline can work with.",
        [
            Field("runtime.convert_sra", "Convert SRA files to FASTQ", "bool",
                  "Turn off only if your files are already FASTQ."),
            Field("runtime.merge_reads", "Join forward and reverse reads", "bool",
                  "Gives one sequence covering the whole marker, which identifies "
                  "species more reliably."),
            Field("runtime.min_merge_overlap", "Smallest overlap when joining", "int",
                  "How many bases the two reads must share. Default 10.",
                  minimum=1, maximum=200),
        ],
    ),
    (
        "Which sequences are kept",
        "The filters applied before anything is looked up.",
        [
            Field("thresholds.min_zotu_size", "Smallest sequence abundance", "int",
                  "Sequences seen fewer times than this are treated as noise.",
                  minimum=1, maximum=100_000),
            Field("runtime.apply_length_filter", "Discard unusual lengths", "bool",
                  "Uses each primer set's length window. Off by default, because "
                  "quoted windows describe the amplicon with its primers still on."),
        ],
    ),
    (
        "Identifying species",
        "Where sequences are looked up, and how good a match has to be.",
        [
            Field("runtime.blast_mode", "Where to search", "choice", "",
                  choices=[
                      ("A TaxaTag reference library", "reference"),
                      ("NCBI, over the internet", "remote"),
                      ("Another BLAST database on this computer", "local"),
                  ]),
            Field("thresholds.min_identity.species", "Name to species at least", "percent",
                  "How close a match must be before a species name is given."),
            Field("thresholds.min_identity.genus", "Name to genus at least", "percent"),
            Field("thresholds.min_identity.family", "Name to family at least", "percent"),
            Field("thresholds.min_query_coverage", "Match must cover at least", "percent",
                  "How much of your sequence has to take part in the match. "
                  "Identity alone will happily report a perfect match over a "
                  "fragment."),
            Field("thresholds.consensus_threshold", "Agreement between references", "float",
                  "How much of the equally good references must agree before a "
                  "name is given. 0.9 means nine in ten.",
                  minimum=0.5, maximum=1.0, decimals=2),
            Field("runtime.blast_max_target_seqs", "Matches to consider", "int",
                  "Never set this to 1: BLAST applies the limit while searching "
                  "rather than after ranking, so asking for one hit can return a "
                  "match that is not the best available.",
                  minimum=1, maximum=500),
            Field("runtime.blast_evalue", "Largest e-value accepted", "float",
                  "How likely a match could have arisen by chance. Smaller is "
                  "stricter.", minimum=0.0, maximum=10.0, decimals=10),
        ],
    ),
    (
        "Searching NCBI",
        "Only used when searching NCBI over the internet.",
        [
            Field("resources.ncbi_email", "Your email address", "text",
                  "NCBI ask for this so they can get in touch about heavy use. "
                  "It is sent only to NCBI."),
            Field("runtime.blast_chunk_size", "Sequences per submission", "int",
                  "A whole survey sent at once gives no sign of progress. "
                  "Default 100.", minimum=1, maximum=5000),
            Field("runtime.blast_timeout_minutes", "Minutes to wait per submission",
                  "float",
                  "How long to wait before moving on. Nothing is lost: the "
                  "search stays NCBI's to finish and Resume collects it.",
                  minimum=0.0, maximum=600.0, decimals=1),
            Field("resources.ncbi_tool", "Name TaxaTag identifies itself by", "text"),
        ],
    ),
    (
        "This computer",
        "How much of the machine an analysis may use.",
        [
            Field("runtime.threads", "Processor cores to use", "int",
                  "0 means use every core.", minimum=0, maximum=256),
            Field("runtime.clean_output", "Delete previous runs when starting", "bool",
                  "Off by default, so an earlier run is never lost."),
            Field("project.name", "Project name", "text",
                  "Recorded in the run manifest, so a result can be traced back."),
        ],
    ),
    (
        "Tool locations",
        "TaxaTag carries its own copies of these and finds them by itself. "
        "Set one only to use a version you installed yourself.",
        [
            Field("paths.vsearch", "VSEARCH", "file"),
            Field("paths.blastn", "BLAST (blastn)", "file"),
            Field("paths.fastqdump", "SRA Toolkit (fasterq-dump)", "file"),
        ],
    ),
]


def _get(data: Dict, path: str) -> Any:
    """Read a dotted path out of the settings, or None if it is not there."""
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set(data: Dict, path: str, value: Any) -> None:
    """Write a dotted path into the settings, making the sections it needs."""
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        if not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


class SettingsFileDialog(QDialog):
    """The whole settings file, shown as a form rather than as text."""

    def __init__(self, path: Optional[Path], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings file")
        self.resize(760, 720)
        self.path: Optional[Path] = Path(path) if path else None
        self.widgets: Dict[str, QWidget] = {}
        #: The settings as edited, available after the dialog is accepted.
        self.result_data: Dict = {}

        self.location = QLabel()
        self.location.setWordWrap(True)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        for title, blurb, fields in GROUPS:
            body_layout.addWidget(self._build_group(title, blurb, fields))
        body_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)

        buttons = QDialogButtonBox()
        open_button = buttons.addButton("Open another file...", QDialogButtonBox.ButtonRole.ResetRole)
        open_button.clicked.connect(self.open_another)
        save_as = buttons.addButton("Save as...", QDialogButtonBox.ButtonRole.ActionRole)
        save_as.clicked.connect(self.save_as)
        buttons.addButton(QDialogButtonBox.StandardButton.Save)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_and_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(HelpLabel(
            "Everything in the settings file, including the parts the Settings "
            "tab does not show. Saving writes a complete file, so a setting you "
            "never touch keeps the value TaxaTag would have used anyway."
        ))
        layout.addWidget(self.location)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)

        self.load(self.path)

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------
    def _build_group(self, title: str, blurb: str, fields: List[Field]) -> QGroupBox:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setSpacing(6)
        if blurb:
            form.addRow(HelpLabel(blurb))
        for entry in fields:
            widget = self._build_widget(entry)
            self.widgets[entry.path] = widget
            form.addRow(entry.label, widget)
            if entry.help:
                form.addRow("", HelpLabel(entry.help))
        return box

    def _build_widget(self, entry: Field) -> QWidget:
        if entry.kind == "bool":
            return QCheckBox()
        if entry.kind == "int":
            widget = QSpinBox()
            widget.setRange(int(entry.minimum), int(entry.maximum))
            return widget
        if entry.kind in ("float", "percent"):
            widget = QDoubleSpinBox()
            if entry.kind == "percent":
                widget.setRange(0.0, 100.0)
                widget.setDecimals(1)
                widget.setSuffix(" %")
            else:
                widget.setRange(entry.minimum, entry.maximum)
                widget.setDecimals(entry.decimals)
            return widget
        if entry.kind == "choice":
            widget = QComboBox()
            for label, value in entry.choices:
                widget.addItem(label, value)
            return widget
        if entry.kind == "folder":
            return PathPicker("Not set", mode="folder")
        if entry.kind == "file":
            return PathPicker("Not set", mode="open")
        return QLineEdit()

    # ------------------------------------------------------------------
    # Loading and saving
    # ------------------------------------------------------------------
    def load(self, path: Optional[Path]) -> None:
        """
        Show a settings file.

        Read through PipelineConfig rather than straight from the YAML, so a
        file that predates a setting shows the value that would really be used
        instead of an empty box.
        """
        try:
            config = PipelineConfig.from_yaml(path) if path and paths.exists(path) else PipelineConfig()
        except Exception as error:  # noqa: BLE001 - show the defaults, say why
            QMessageBox.warning(
                self, "Settings could not be read",
                f"{path}\n\n{error}\n\nShowing the default settings instead.",
            )
            config = PipelineConfig()

        self.path = Path(path) if path else None
        self.data = config.to_dict()
        self.location.setText(
            f"Editing: {self.path}" if self.path else "Editing settings that have not been saved yet"
        )

        for group in GROUPS:
            for entry in group[2]:
                self._show(entry, _get(self.data, entry.path))

    def _show(self, entry: Field, value: Any) -> None:
        widget = self.widgets[entry.path]
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QSpinBox):
            widget.setValue(int(value or 0))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value or 0.0))
        elif isinstance(widget, QComboBox):
            index = widget.findData(value)
            widget.setCurrentIndex(max(0, index))
        elif isinstance(widget, PathPicker):
            widget.set_value("" if value in (None, "None") else str(value))
        else:
            widget.setText("" if value is None else str(value))

    def collect(self) -> Dict:
        """The settings as they now stand in the form."""
        data = dict(self.data)
        for group in GROUPS:
            for entry in group[2]:
                _set(data, entry.path, self._read(entry))
        return data

    def _read(self, entry: Field) -> Any:
        widget = self.widgets[entry.path]
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QSpinBox):
            return widget.value()
        if isinstance(widget, QDoubleSpinBox):
            return widget.value()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, PathPicker):
            return widget.value()
        return widget.text()

    def open_another(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Open settings",
            str(self.path.parent if self.path else Path.home()),
            "Settings files (*.yaml *.yml)",
        )
        if chosen:
            self.load(Path(chosen))

    def save_as(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Save settings",
            str(self.path or Path.home() / "taxatag_settings.yaml"),
            "Settings files (*.yaml *.yml)",
        )
        if chosen:
            self.path = Path(chosen)
            self.accept_and_save()

    def accept_and_save(self) -> None:
        self.result_data = self.collect()
        if self.path and not self._write(self.path, self.result_data):
            return
        self.accept()

    def _write(self, path: Path, data: Dict) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)
            return True
        except OSError as error:
            QMessageBox.critical(self, "Settings could not be saved", f"{path}\n\n{error}")
            return False

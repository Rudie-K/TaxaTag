# src/gui/results.py
"""
Showing the finished species table inside the application.

The results are written to CSV either way, but making the user open a
spreadsheet to find out whether the run worked is a poor ending. This panel
shows what was found, lets it be filtered, and points at the files.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.gui.widgets import HelpLabel
from src.reference import contaminants
from src.utils import platform as platform_utils

#: Columns worth showing first. The sequence itself is long and is kept last.
PREFERRED_ORDER = [
    "Sample", "Locus", "Scientific_Name", "Rank", "Identity_Percent",
    "Reads", "Percent_Of_Sample", "Class", "Order", "Family", "Genus", "Species",
    "Accession", "TaxID", "ZOTU",
]


class ResultsPanel(QWidget):
    """The species table from the most recent run."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: List[List[str]] = []
        self._headers: List[str] = []
        self._table_path: Optional[Path] = None
        self._run_dir: Optional[Path] = None
        self._output_base: Optional[Path] = None

        self.heading = QLabel("No results yet")
        self.heading.setObjectName("sectionLabel")
        self.subheading = HelpLabel("Run an analysis and the species found will appear here.")

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by species, sample or anything else...")
        self.search.textChanged.connect(self._apply_filter)
        self.search.setEnabled(False)

        self.sample_filter = QComboBox()
        self.sample_filter.addItem("All samples", None)
        self.sample_filter.currentIndexChanged.connect(self._apply_filter)
        self.sample_filter.setEnabled(False)

        self.table = QTableWidget(0, 0)
        self.table.setAccessibleName("Species found, one row per taxon per sample")
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        self.open_folder = QPushButton("Open results folder")
        self.open_folder.clicked.connect(self._open_folder)
        self.open_folder.setEnabled(False)
        # Off by default. A results panel that quietly omits detections would
        # be worse than one that shows too much, so hiding anything has to be
        # something the user switched on and can see is on.
        self.hide_contaminants = QCheckBox("Hide likely contamination")
        self.hide_contaminants.setToolTip(
            "Human, livestock and pets - the DNA that arrives with the person "
            "taking the sample rather than from the water. Nothing is deleted: "
            "the saved table still holds every detection."
        )
        self.hide_contaminants.stateChanged.connect(self._apply_filter)

        self.export = QPushButton("Save a copy of this table...")
        self.export.clicked.connect(self._export)
        self.export.setEnabled(False)

        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.sample_filter)
        filters.addWidget(self.hide_contaminants)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.export)
        buttons.addWidget(self.open_folder)

        # Which run is being shown, and a way back to the earlier ones.
        #
        # Until now the only results reachable were the ones from the run you
        # had just watched: close the window, or start a second analysis, and
        # a finished survey could only be opened by finding the folder and
        # loading a CSV somewhere else. The results were never lost, only
        # unreachable from the program that produced them.
        self.run_picker = QComboBox()
        self.run_picker.setMinimumWidth(340)
        self.run_picker.activated.connect(self._run_chosen)

        chooser = QHBoxLayout()
        chooser.addWidget(QLabel("Showing"))
        chooser.addWidget(self.run_picker, 1)
        chooser.addStretch(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(chooser)
        layout.addWidget(self.heading)
        layout.addWidget(self.subheading)
        layout.addLayout(filters)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    # Earlier runs
    # ------------------------------------------------------------------
    def set_results_folder(self, output_base) -> None:
        """
        List the finished runs under `output_base`, newest first.

        Only runs with a species table are offered. A run that failed, was
        stopped, or is still going has a folder and no answer in it, and
        putting it in a list of results would promise something the folder
        cannot deliver - `Resume` is where an unfinished run belongs.
        """
        from src.pipeline import layout

        self._output_base = Path(output_base) if output_base else None
        current = self.run_picker.currentData()

        self.run_picker.blockSignals(True)
        self.run_picker.clear()

        found = 0
        if self._output_base:
            for run_dir in layout.runs_by_recency(self._output_base):
                table = self._table_in(run_dir)
                if table is None:
                    continue
                self.run_picker.addItem(self._label_for(run_dir), str(table))
                found += 1

        if not found:
            self.run_picker.addItem("No finished runs yet", "")
        self.run_picker.setEnabled(found > 0)

        if current:
            at = self.run_picker.findData(current)
            if at >= 0:
                self.run_picker.setCurrentIndex(at)
        self.run_picker.blockSignals(False)

        # Show the newest run rather than naming it and leaving the table
        # empty. The picker said "2026-09-10  105945" above a panel reading
        # "No results yet", which does not look like a control waiting to be
        # used - it looks like that run found nothing.
        #
        # Only when nothing is loaded yet, so this never pulls the view away
        # from a run the user chose.
        if found and self._table_path is None:
            self._run_chosen(self.run_picker.currentIndex())

    @staticmethod
    def _table_in(run_dir: Path) -> Optional[Path]:
        """
        The species table in a run folder, preferring the one with lineages.

        Both are written, and the taxonomy one is what somebody wants: the
        plain table names what was found, and this one says what it is.
        """
        from src.pipeline import layout

        for name in (layout.FINAL_TAXONOMY_TABLE, layout.FINAL_SPECIES_TABLE):
            # layout.IDENTIFICATION rather than the literal, so renaming the
            # folder in one place cannot leave the results tab looking in
            # the old one and reporting every past run as unfinished.
            candidate = layout.identification_dir(Path(run_dir)) / name
            try:
                if candidate.exists():
                    return candidate
            except OSError:
                continue
        return None

    @staticmethod
    def _label_for(run_dir: Path) -> str:
        """
        A run as a person would refer to it: when it ran, and what it was.

        The folder name carries both - a timestamp, and whatever the user
        chose to call it - so it is shown rather than reformatted. Rewriting
        it into prose would make the list and the folders on disk disagree
        about what a run is called, which is the one thing that has to match
        when somebody goes looking for the files.
        """
        return Path(run_dir).name.replace("_", "  ", 1)

    def _run_chosen(self, index: int) -> None:
        table = self.run_picker.itemData(index)
        if not table:
            return
        path = Path(table)
        # The run folder is two levels up from 05_results/<table>.
        self.load(path, path.parent.parent)

    # ------------------------------------------------------------------
    def load(self, table_path: Path, run_dir: Optional[Path] = None) -> None:
        """Read a species table produced by a run and display it."""
        import csv

        self._table_path = Path(table_path)
        self._run_dir = Path(run_dir) if run_dir else self._table_path.parent

        try:
            with open(self._table_path, "r", newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                self._headers = next(reader, [])
                self._rows = [row for row in reader if row]
        except OSError as error:
            self.heading.setText("Results could not be read")
            self.subheading.setText(str(error))
            return

        self._headers, self._rows = _reorder(self._headers, self._rows)

        species_column = _index_of(self._headers, "Scientific_Name")
        sample_column = _index_of(self._headers, "Sample")
        distinct_species = (
            len({row[species_column] for row in self._rows if len(row) > species_column})
            if species_column is not None
            else 0
        )
        samples = (
            sorted({row[sample_column] for row in self._rows if len(row) > sample_column})
            if sample_column is not None
            else []
        )

        self.heading.setText(
            f"{distinct_species} taxa found across {len(samples)} sample(s)"
        )
        self.subheading.setText(f"From {self._table_path.name}")

        self.sample_filter.blockSignals(True)
        self.sample_filter.clear()
        self.sample_filter.addItem("All samples", None)
        for sample in samples:
            self.sample_filter.addItem(sample, sample)
        self.sample_filter.blockSignals(False)

        for widget in (self.search, self.sample_filter, self.hide_contaminants,
                       self.open_folder, self.export):
            widget.setEnabled(True)

        self._populate(self._rows)

    def _populate(self, rows: List[List[str]]) -> None:
        # Sorting has to be off while filling, or Qt reorders rows underneath
        # the loop and the table ends up scrambled.
        self.table.setSortingEnabled(False)
        self.table.setColumnCount(len(self._headers))
        self.table.setHorizontalHeaderLabels([h.replace("_", " ") for h in self._headers])
        self.table.setRowCount(len(rows))

        numeric_columns = {
            _index_of(self._headers, name)
            for name in ("Identity_Percent", "Reads", "Percent_Of_Sample")
        } - {None}

        for row_index, row in enumerate(rows):
            for column in range(len(self._headers)):
                value = row[column] if column < len(row) else ""
                item = QTableWidgetItem()
                if column in numeric_columns:
                    try:
                        item.setData(Qt.ItemDataRole.DisplayRole, float(value))
                    except (TypeError, ValueError):
                        item.setText(value)
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                else:
                    item.setText(value)
                self.table.setItem(row_index, column, item)

        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        sequence_column = _index_of(self._headers, "Sequence")
        if sequence_column is not None:
            self.table.setColumnWidth(sequence_column, 220)
            header.setSectionResizeMode(sequence_column, QHeaderView.ResizeMode.Interactive)

    def _apply_filter(self) -> None:
        text = self.search.text().strip().lower()
        sample = self.sample_filter.currentData()
        sample_column = _index_of(self._headers, "Sample")

        rows = self._rows
        if sample and sample_column is not None:
            rows = [r for r in rows if len(r) > sample_column and r[sample_column] == sample]
        if text:
            rows = [r for r in rows if any(text in str(cell).lower() for cell in r)]

        hidden = []
        if self.hide_contaminants.isChecked():
            rows, hidden = self._without_contaminants(rows)

        self._populate(rows)
        if hidden:
            # Say what was hidden and what it was, so the count on screen is
            # never quietly different from the count in the file.
            what = contaminants.summarise(
                r[_index_of(self._headers, "Scientific_Name") or 0] for r in hidden
            )
            self.subheading.setText(
                f"Showing {len(rows)} of {len(self._rows)} rows - "
                f"{len(hidden)} hidden as contamination"
                + (f" ({what})" if what else "")
            )
        elif len(rows) != len(self._rows):
            self.subheading.setText(f"Showing {len(rows)} of {len(self._rows)} rows")
        else:
            self.subheading.setText(f"From {self._table_path.name}" if self._table_path else "")

    def _without_contaminants(self, rows: List[List[str]]):
        """Split rows into what to show and what the toggle is hiding."""
        return contaminants.split(
            rows,
            _index_of(self._headers, "Scientific_Name"),
            _index_of(self._headers, "Genus"),
        )

    def _open_folder(self) -> None:
        if self._run_dir and not platform_utils.open_in_file_manager(self._run_dir):
            QMessageBox.information(self, "Results folder", f"Your results are in:\n{self._run_dir}")

    def _export(self) -> None:
        if not self._table_path:
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Save a copy",
            str(Path.home() / self._table_path.name),
            "Spreadsheet (*.csv)",
        )
        if not chosen:
            return

        # Only asked when the toggle is on. Someone who has not hidden anything
        # has not been shown a reason to think about it, and a question out of
        # nowhere about removing species from their results would be alarming.
        drop_contaminants = False
        if self.hide_contaminants.isChecked():
            _, hidden = self._without_contaminants(self._rows)
            if hidden:
                what = contaminants.summarise(
                    r[_index_of(self._headers, "Scientific_Name") or 0] for r in hidden
                )
                answer = QMessageBox.question(
                    self,
                    "Leave the contamination out?",
                    f"{len(hidden)} row(s) are hidden here as contamination"
                    + (f" ({what})" if what else "")
                    + ".\n\nLeave them out of the saved file as well, or save "
                    "the complete table?\n\nThe run's own results folder keeps "
                    "the complete table either way.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                drop_contaminants = answer == QMessageBox.StandardButton.Yes

        try:
            if drop_contaminants:
                self._write_filtered(Path(chosen))
                QMessageBox.information(
                    self, "Saved",
                    f"Saved to:\n{chosen}\n\nContamination was left out. The "
                    "complete table is still in the results folder.",
                )
            else:
                import shutil

                shutil.copy2(self._table_path, chosen)
                QMessageBox.information(self, "Saved", f"Saved to:\n{chosen}")
        except OSError as error:
            QMessageBox.critical(self, "Not saved", str(error))

    def _write_filtered(self, destination: Path) -> None:
        """
        Write the table without the contaminant rows.

        Filtered from the file on disk rather than from the rows held for
        display, because this panel reorders columns to put the useful ones
        first. Writing what is shown would produce a file whose columns were
        arranged differently from the one saved when nothing is filtered - two
        exports of the same results that do not look like the same table.
        """
        import csv

        if not self._table_path:
            return
        with open(self._table_path, newline="", encoding="utf-8") as source:
            reader = csv.reader(source)
            headers = next(reader, [])
            name_column = _index_of(headers, "Scientific_Name")
            genus_column = _index_of(headers, "Genus")
            kept, _ = contaminants.split(reader, name_column, genus_column)

        with open(destination, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(kept)


def _index_of(headers: List[str], name: str) -> Optional[int]:
    try:
        return headers.index(name)
    except ValueError:
        return None


def _reorder(headers: List[str], rows: List[List[str]]) -> tuple[List[str], List[List[str]]]:
    """Put the most useful columns first, keeping any others after them."""
    order = [h for h in PREFERRED_ORDER if h in headers]
    order += [h for h in headers if h not in order]
    if order == headers:
        return headers, rows

    positions = [headers.index(h) for h in order]
    moved = [[row[p] if p < len(row) else "" for p in positions] for row in rows]
    return order, moved

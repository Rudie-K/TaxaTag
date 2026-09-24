# src/gui/analysis.py
"""
The Analysis tab: a finished run's standard summaries, in the window.

Laid out like RStudio, VS Code or a chat window, so it is familiar: the run
and its sample sheet across the top, a list of analyses on the left, the
chosen result on the right in Table, Chart and Notes tabs, and a status
line along the bottom. **Run** computes; **Save** keeps. What each analysis
does is `src/analysis/workbench.py`; this is only the window around it
(decision 0039; `planned.md` item 4).

Every word shown is conversational English or the field's term
(`writing-the-interface.md` rule 10), and every analysis carries its
readiness: greyed but reachable when it cannot run, marked in the warning
colour *and* with a sign when it can but should be read with care, with the
exact reason in its tooltip (rule 9, decision 0034).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, QSortFilterProxyModel, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFontInfo, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QStackedWidget, QTableView, QTabWidget, QVBoxLayout,
    QWidget,
)

from src.analysis import diversity, readiness, sites, workbench
from src.gui import theme
from src.pipeline import layout
from src.utils import platform as platform_utils

#: The rank choices, in the words a user would use, and what each means to the analyses.
RANKS = (("As identified", diversity.MIXED), ("Species", "species"), ("Genus", "genus"), ("Family", "family"))

#: Marks a warning in the list, so it is never shown by colour alone (0024).
WARNING_SIGN = "⚠"

HEADER_ROLE = Qt.ItemDataRole.UserRole + 1
SORT_ROLE = Qt.ItemDataRole.UserRole + 2


# ---------------------------------------------------------------- the table


def _display(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-0") else "0"
    return str(value)


def _sortable(value):
    """Numbers sort as numbers, text as text; blanks go last either way."""
    if value is None or value == "":
        return (1, 0.0, "")
    try:
        return (0, float(value), "")
    except (TypeError, ValueError):
        return (0, float("inf"), str(value).lower())


class RowsModel(QAbstractTableModel):
    """A result table. Only the rows in view are drawn, so tens of thousands of rows stay quick."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns: List[str] = []
        self.rows: List[Dict[str, object]] = []

    def set_table(self, columns: List[str], rows: List[Dict[str, object]]) -> None:
        self.beginResetModel()
        self.columns, self.rows = list(columns), list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        value = self.rows[index.row()].get(self.columns[index.column()])
        if role == Qt.ItemDataRole.DisplayRole:
            return _display(value)
        if role == SORT_ROLE:
            return _sortable(value)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section].replace("_", " ")
        return None


class SortingProxy(QSortFilterProxyModel):
    """Sorts by value, not by the text shown, and searches every column."""

    def lessThan(self, left, right) -> bool:
        return left.data(SORT_ROLE) < right.data(SORT_ROLE)


# ---------------------------------------------------------------- the chart


def _tick_step(highest: float) -> float:
    """A round step (1, 2 or 5 times a power of ten) giving four to eight ticks up to `highest`."""
    step = 1.0
    while highest / step > 8:
        for factor in (2, 2.5, 2):
            step *= factor
            if highest / step <= 8:
                break
    return step


class ChartWidget(QWidget):
    """
    The species accumulation curve, drawn by the program itself: interpolated
    as a solid line, extrapolated as a dashed one, with the 95% interval as a
    band. In preview mode it is the small copy at the foot of the list, and
    clicking it (or pressing Enter) opens the full chart.
    """

    clicked = pyqtSignal()

    def __init__(self, preview: bool = False, parent=None):
        super().__init__(parent)
        self.preview = preview
        self.curve: Optional[workbench.Curve] = None
        self.caption = ""
        self.palette_override: Optional[Dict[str, str]] = None
        self.scale = 1.0        # text, margins and lines together, for the saved copy
        self.setMinimumHeight(90 if preview else 260)
        if preview:
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("The last chart. Click to open it.")

    def set_curve(self, curve: Optional[workbench.Curve], caption: str = "") -> None:
        self.curve, self.caption = curve, caption
        described = (f"Species accumulation curve, {caption}" if curve else "No chart yet")
        self.setAccessibleName(("Preview of the last chart: " if self.preview else "") + described)
        self.update()

    def mousePressEvent(self, event) -> None:
        if self.preview and self.curve:
            self.clicked.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if self.preview and self.curve and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        colours = self.palette_override or theme.active()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(colours["panel"]))
        if not self.curve:
            painter.setPen(QColor(colours["muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "" if self.preview else "Run the analysis to draw its chart.")
            return
        s = self.scale
        left, bottom = (6, 6) if self.preview else (56 * s, 44 * s)
        top, right = (6, 6) if self.preview else (20 * s, 20 * s)
        area = QRectF(left, top, max(10, self.width() - left - right), max(10, self.height() - top - bottom))
        points = self.curve.points
        x_max = max(p[0] for p in points)
        step = _tick_step(max(p[3] for p in points))
        y_max = step * (int(max(p[3] for p in points) / step) + 1)

        def at(x, y):
            return QPointF(area.left() + area.width() * (x / x_max), area.bottom() - area.height() * (y / y_max))

        accent = QColor(colours["accent"])
        band = QColor(accent)
        band.setAlpha(45)
        outline = QPainterPath(at(points[0][0], points[0][3]))
        for x, _, _, upper, _ in points[1:]:
            outline.lineTo(at(x, upper))
        for x, _, lower, _, _ in reversed(points):
            outline.lineTo(at(x, lower))
        outline.closeSubpath()
        painter.fillPath(outline, band)

        for dashed in (False, True):
            pen = QPen(accent, 2 * s)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            chosen = [p for p in points if (p[0] >= self.curve.observed_at) == dashed or p[0] == self.curve.observed_at]
            for a, b in zip(chosen, chosen[1:]):
                painter.drawLine(at(a[0], a[1]), at(b[0], b[1]))
        observed = next(p for p in points if p[0] == self.curve.observed_at)
        painter.setBrush(accent)
        painter.drawEllipse(at(observed[0], observed[1]), 3.5 * s, 3.5 * s)

        if self.preview:
            return
        painter.setPen(QColor(colours["border"]))
        painter.drawLine(area.bottomLeft(), area.bottomRight())
        painter.drawLine(area.bottomLeft(), area.topLeft())
        painter.setPen(QColor(colours["text"]))
        font = self.font()
        font.setPixelSize(round(QFontInfo(font).pixelSize() * s))
        painter.setFont(font)
        line = 16 * s
        for i in range(0, int(round(y_max / step)) + 1):
            y = step * i
            point = at(0, y)
            painter.drawText(QRectF(0, point.y() - line / 2, left - 8 * s, line),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{y:.0f}")
        for x in sorted({1, self.curve.observed_at, x_max}):
            point = at(x, 0)
            painter.drawText(QRectF(point.x() - 3 * line, area.bottom() + 4 * s, 6 * line, line), Qt.AlignmentFlag.AlignCenter, str(x))
        painter.drawText(QRectF(area.left(), self.height() - 1.3 * line, area.width(), 1.2 * line), Qt.AlignmentFlag.AlignCenter,
                         f"Number of {self.curve.unit}s   (solid: rarefied, dashed: extrapolated, band: 95% interval)")
        painter.save()
        painter.translate(0.75 * line, area.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-4 * line, -line / 2, 8 * line, line), Qt.AlignmentFlag.AlignCenter, "Taxa")
        painter.restore()


#: The saved chart's name and size: large enough to print, in the light
#: palette whatever is on screen, since a figure goes on a white page.
CHART_FILE = "species_accumulation_curve.png"
CHART_SIZE = (1600, 1000)


def write_chart(curve: workbench.Curve, caption: str, path: Path) -> Path:
    """Draw `curve` to a PNG at `path`, as the Chart tab shows it but always light."""
    chart = ChartWidget()
    chart.palette_override = theme.PALETTES[theme.LIGHT]
    chart.set_curve(curve, caption)
    chart.resize(*CHART_SIZE)
    chart.scale = 2.0
    # Rendered into an image of fixed pixels, not grabbed: `grab` follows the
    # screen's scaling, and a saved figure should not depend on the monitor.
    image = QImage(*CHART_SIZE, QImage.Format.Format_ARGB32)
    chart.render(image)
    image.save(str(path), "PNG")
    chart.deleteLater()
    return path


# ---------------------------------------------------------------- running off the window's thread


class AnalysisWorker(QThread):
    """Runs one analysis without freezing the window; the bootstrap can take seconds."""

    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, spec: workbench.Spec, context: workbench.Context, parent=None):
        super().__init__(parent)
        self.spec, self.context = spec, context

    def run(self) -> None:
        try:
            self.completed.emit(workbench.run(self.spec, self.context))
        except Exception as error:  # noqa: BLE001 - reported in the status line, never a crash
            self.failed.emit(str(error))


# ---------------------------------------------------------------- the tab


class AnalysisPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._output_base: Optional[Path] = None
        self._sheet_path: Optional[Path] = None
        self._sheet: Optional[sites.SampleSheet] = None
        self._library = None                       # the chosen run's own reference library
        self._species_list = None
        self._review_sheet: Optional[Path] = None
        self._outcomes: Dict[str, workbench.Outcome] = {}
        self._statuses: Dict[str, tuple] = {}
        self._chart_outcome: Optional[workbench.Outcome] = None
        self._worker: Optional[AnalysisWorker] = None
        self._build()
        self._show_selected()

    # ------------------------------------------------------------ building

    def _build(self) -> None:
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.run_picker = QComboBox()
        self.run_picker.setAccessibleName("Run")
        self.run_picker.setMinimumContentsLength(22)
        self.run_picker.currentIndexChanged.connect(lambda _: self._context_changed())
        self.sheet_label = QLabel("None")
        self.sheet_button = QPushButton("Choose…")
        self.sheet_button.setAccessibleName("Choose a sample sheet")
        self.sheet_button.clicked.connect(self._choose_sheet)
        self.sheet_clear = QPushButton("Clear")
        self.sheet_clear.setAccessibleName("Clear the sample sheet")
        self.sheet_clear.clicked.connect(lambda: self.set_sheet(None))
        self.sample_column = QComboBox()
        self.sample_column.setAccessibleName("Sample column")
        self.site_column = QComboBox()
        self.site_column.setAccessibleName("Site column")
        for combo in (self.sample_column, self.site_column):
            combo.currentIndexChanged.connect(lambda _: self._load_sheet())
        self.marker_picker = QComboBox()
        self.marker_picker.setAccessibleName("Marker")
        self.marker_picker.currentIndexChanged.connect(lambda _: self._context_changed(keep_markers=True))
        self.sample_column_label = QLabel("Sample column")
        self.site_column_label = QLabel("Site column")
        for widget in (QLabel("Run"), self.run_picker, QLabel("Marker"), self.marker_picker):
            bar.addWidget(widget)
        bar.addStretch(1)

        # What the run is read against, on a row of its own: the two sheets
        # and their columns would not fit beside the run on a laptop screen.
        self.list_label = QLabel("None")
        self.list_button = QPushButton("Choose…")
        self.list_button.setAccessibleName("Choose a species list")
        self.list_button.setToolTip("A CSV of the species that could be there, with a ScientificName column and, "
                                    "if you have them, Synonyms and Habitat.")
        self.list_button.clicked.connect(self._choose_list)
        self.list_clear = QPushButton("Clear")
        self.list_clear.setAccessibleName("Clear the species list")
        self.list_clear.clicked.connect(lambda: self.set_species_list(None))
        inputs = QHBoxLayout()
        inputs.setSpacing(8)
        for widget in (QLabel("Sample sheet"), self.sheet_label, self.sheet_button, self.sheet_clear,
                       self.sample_column_label, self.sample_column, self.site_column_label, self.site_column,
                       QLabel("Species list"), self.list_label, self.list_button, self.list_clear):
            inputs.addWidget(widget)
        inputs.addStretch(1)

        # The list, and the preview of the last chart at its foot.
        self.list = QListWidget()
        self.list.setAccessibleName("Analyses")
        self.list.currentItemChanged.connect(lambda *_: self._show_selected())
        group = None
        for spec in workbench.SPECS:
            if spec.group != group:
                group = spec.group
                header = QListWidgetItem(group)
                header.setFlags(Qt.ItemFlag.ItemIsEnabled)
                header.setData(HEADER_ROLE, True)
                self.list.addItem(header)
            item = QListWidgetItem(f"{spec.title}\n{spec.subtitle}")
            item.setData(Qt.ItemDataRole.UserRole, spec.key)
            self.list.addItem(item)
        self.preview = ChartWidget(preview=True)
        self.preview.clicked.connect(self._open_chart)
        self.preview_label = QLabel("Last chart")
        self.preview_label.setObjectName("helpLabel")
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.list, 1)
        left_layout.addWidget(self.preview_label)
        left_layout.addWidget(self.preview)

        # The result: options, Run and Save, then Table / Chart / Notes.
        options = QHBoxLayout()
        self.rank_picker = QComboBox()
        for label, basis in RANKS:
            self.rank_picker.addItem(label, basis)
        self.rank_picker.setAccessibleName("Taxonomic rank")
        self.rank_picker.setToolTip("As identified counts each name at the rank TaxaTag gave it, and counts a "
                                    "genus only when none of its species was found in the same sample.")
        self.contaminants = QCheckBox("Include likely contaminants")
        self.contaminants.setToolTip("Genera commonly found in laboratory reagents are set aside unless this "
                                     "is ticked. They are never deleted (decision 0012).")
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_000_000_000)
        self.seed.setValue(workbench.effort.SEED)
        self.seed.setAccessibleName("Random seed")
        self.seed.setMaximumWidth(self.seed.fontMetrics().horizontalAdvance("2000000000") + 48)
        self.seed.setToolTip("Where the bootstrap starts. The same seed always gives the same intervals.")
        self.seed_label = QLabel("Random seed")
        for widget in (self.rank_picker, self.contaminants, self.seed):
            signal = widget.stateChanged if isinstance(widget, QCheckBox) else (
                widget.valueChanged if isinstance(widget, QSpinBox) else widget.currentIndexChanged)
            signal.connect(lambda *_: self._options_changed())
        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self.run_selected)
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._save_clicked)
        self.rank_label = QLabel("Taxonomic rank")
        self.review_label = QLabel("Filled review sheet")
        self.review_name = QLabel("None")
        self.review_button = QPushButton("Choose…")
        self.review_button.setAccessibleName("Choose a filled review sheet")
        self.review_button.clicked.connect(self._choose_review_sheet)
        for widget in (self.rank_label, self.rank_picker, self.contaminants, self.seed_label, self.seed,
                       self.review_label, self.review_name, self.review_button):
            options.addWidget(widget)
        options.addStretch(1)
        options.addWidget(self.run_button)
        options.addWidget(self.save_button)

        self.table_picker = QComboBox()
        self.table_picker.setAccessibleName("Table shown")
        self.table_picker.currentIndexChanged.connect(lambda _: self._show_table())
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setAccessibleName("Search the table")
        self.model = RowsModel(self)
        self.proxy = SortingProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAccessibleName("Result table")
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.table.horizontalHeader().sectionClicked.connect(
            lambda _: self.table.horizontalHeader().setSortIndicatorShown(True))
        table_page = QWidget()
        table_layout = QVBoxLayout(table_page)
        tools = QHBoxLayout()
        tools.addWidget(self.table_picker)
        tools.addStretch(1)
        tools.addWidget(self.search)
        table_layout.addLayout(tools)
        table_layout.addWidget(self.table, 1)
        self.chart = ChartWidget()
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setAccessibleName("Notes: cautions and how the numbers were made")
        self.result_tabs = QTabWidget()
        self.result_tabs.addTab(table_page, "Table")
        self.result_tabs.addTab(self.chart, "Chart")
        self.result_tabs.addTab(self.notes, "Notes")
        self.placeholder = QLabel()
        self.placeholder.setWordWrap(True)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.result_tabs)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(options)
        right_layout.addWidget(self.stack, 1)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 800])

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.open_button = QPushButton("Open folder")
        self.open_button.clicked.connect(self._open_saved)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.open_button)

        outer = QVBoxLayout(self)
        outer.addLayout(bar)
        outer.addLayout(inputs)
        outer.addWidget(splitter, 1)
        outer.addLayout(status_row)
        self._set_sheet_widgets()
        self._set_list_widgets()
        self._refresh_preview()

    # ------------------------------------------------------------ the run bar

    def set_results_folder(self, output_base) -> None:
        """List the finished runs under the results folder, newest first, as the Results tab does."""
        from src.gui.results import ResultsPanel

        self._output_base = Path(output_base) if output_base else None
        current = self.run_picker.currentData()
        self.run_picker.blockSignals(True)
        self.run_picker.clear()
        if self._output_base:
            for run_dir in layout.runs_by_recency(self._output_base):
                if ResultsPanel._table_in(run_dir) is not None:
                    self.run_picker.addItem(ResultsPanel._label_for(run_dir), str(run_dir))
        if self.run_picker.count() == 0:
            self.run_picker.addItem("No finished runs yet", "")
        at = self.run_picker.findData(current) if current else -1
        self.run_picker.setCurrentIndex(max(at, 0))
        self.run_picker.setEnabled(bool(self.run_picker.currentData()))
        self.run_picker.blockSignals(False)
        self._context_changed()

    def run_dir(self) -> Optional[Path]:
        data = self.run_picker.currentData()
        return Path(data) if data else None

    def _choose_sheet(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a sample sheet", str(self._output_base or ""),
                                              "Sample sheets (*.csv);;All files (*)")
        if path:
            self.set_sheet(Path(path))

    def set_sheet(self, path: Optional[Path]) -> None:
        """Take a sample sheet, offering its columns, with the one that names this run's samples first."""
        self._sheet_path = Path(path) if path else None
        self.sample_column.blockSignals(True)
        self.site_column.blockSignals(True)
        self.sample_column.clear()
        self.site_column.clear()
        if self._sheet_path:
            try:
                with open(self._sheet_path, encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
            except (OSError, UnicodeDecodeError, csv.Error) as problem:
                self._sheet_path = None
                self._say(f"That sample sheet could not be read: {problem}", warning=True)
                rows = []
            columns = list(rows[0]) if rows else []
            samples = set()
            if self.run_dir():
                samples = {row["Sample"] for row in diversity.read_table(self.run_dir()) if row.get("Sample")}
            matching = max(columns, key=lambda c: len({(r.get(c) or "").strip() for r in rows} & samples),
                           default=None)
            ordered = ([matching] if matching else []) + [c for c in columns if c != matching]
            self.sample_column.addItems(ordered)
            sites_first = (["Site"] if "Site" in columns else []) + [c for c in columns if c != "Site"]
            self.site_column.addItems([c for c in sites_first if c != matching] or sites_first)
        self.sample_column.blockSignals(False)
        self.site_column.blockSignals(False)
        self._set_sheet_widgets()
        self._load_sheet()

    def _choose_list(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a species list", str(self._output_base or ""),
                                              "Species lists (*.csv);;All files (*)")
        if path:
            self.set_species_list(Path(path))

    def set_species_list(self, path: Optional[Path]) -> None:
        from src.analysis.adjudication import SpeciesList

        self._species_list = None
        if path:
            try:
                loaded = SpeciesList.load(Path(path))
            except (OSError, UnicodeDecodeError, ValueError) as problem:
                self._say(f"That species list could not be read: {problem}", warning=True)
            else:
                if loaded.accepted:
                    self._species_list = loaded
                else:
                    self._say("That file names no species: it needs a ScientificName column.", warning=True)
        self._set_list_widgets()
        self._context_changed(keep_markers=True)

    def _set_list_widgets(self) -> None:
        chosen = self._species_list
        self.list_label.setText(f"{Path(chosen.source).name} ({len(chosen.accepted):,} species)" if chosen else "None")
        self.list_clear.setVisible(chosen is not None)

    def _choose_review_sheet(self) -> None:
        start = str(self.run_dir() or self._output_base or "")
        path, _ = QFileDialog.getOpenFileName(self, "Choose a filled review sheet", start,
                                              "Review sheets (*.csv);;All files (*)")
        if path:
            self.set_review_sheet(Path(path))

    def set_review_sheet(self, path: Optional[Path]) -> None:
        self._review_sheet = Path(path) if path else None
        self.review_name.setText(self._review_sheet.name if self._review_sheet else "None")
        self._context_changed(keep_markers=True)

    def _set_sheet_widgets(self) -> None:
        chosen = self._sheet_path is not None
        self.sheet_label.setText(self._sheet_path.name if chosen else "None")
        for widget in (self.sheet_clear, self.sample_column, self.site_column, self.sample_column_label,
                       self.site_column_label):
            widget.setVisible(chosen)

    def _load_sheet(self) -> None:
        self._sheet = None
        if self._sheet_path and self.sample_column.currentText() and self.site_column.currentText():
            try:
                self._sheet = sites.SampleSheet.load(self._sheet_path, self.sample_column.currentText(),
                                                     self.site_column.currentText())
                if self.run_dir():
                    samples = {row["Sample"] for row in diversity.read_table(self.run_dir()) if row.get("Sample")}
                    sites.check(self._sheet, samples)
            except (sites.SheetError, ValueError) as problem:
                self._sheet = None
                self._say(str(problem), warning=True)
        self._context_changed(keep_markers=True)

    # ------------------------------------------------------------ what can run

    def context(self) -> Optional[workbench.Context]:
        run_dir = self.run_dir()
        if run_dir is None:
            return None
        return workbench.Context(run_dir, self.marker_picker.currentData() or "", self._sheet,
                                 self.rank_picker.currentData(), self.contaminants.isChecked(), self.seed.value(),
                                 self._library, self._species_list, self._review_sheet)

    def _context_changed(self, keep_markers: bool = False) -> None:
        run_dir = self.run_dir()
        if not keep_markers:
            # The library the run was searched against, as the terminal finds it.
            if self._library is not None:
                self._library.close()
            self._library = workbench.library_for(run_dir) if run_dir else None
            self.marker_picker.blockSignals(True)
            self.marker_picker.clear()
            for locus in (workbench.loci(run_dir) if run_dir else []):
                self.marker_picker.addItem(locus, locus)
            self.marker_picker.blockSignals(False)
        self._forget_results()
        context = self.context()
        try:
            self._statuses = workbench.statuses(context) if context else {}
        except (sites.SheetError, ValueError) as problem:
            self._statuses = {}
            self._say(str(problem), warning=True)
        self._paint_list()
        self._show_selected()

    def _options_changed(self) -> None:
        # A result made with other options no longer matches what the row says.
        self._context_changed(keep_markers=True)

    def _forget_results(self) -> None:
        for outcome in self._outcomes.values():
            if outcome.saved_to is None:
                workbench.discard(outcome)
        self._outcomes.clear()

    def _paint_list(self) -> None:
        colours = theme.active()
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(HEADER_ROLE):
                item.setForeground(QColor(colours["muted"]))
                continue
            spec = workbench.BY_KEY[item.data(Qt.ItemDataRole.UserRole)]
            status, reasons = self._statuses.get(spec.key, (workbench.AVAILABLE, []))
            sign = f"{WARNING_SIGN} " if status == readiness.WARNING else ""
            item.setText(f"{sign}{spec.title}\n{spec.subtitle}")
            colour = {readiness.BLOCKED: colours["text_disabled"], readiness.WARNING: colours["log_warning"]}.get(
                status, colours["text"])
            item.setForeground(QColor(colour))
            words = {readiness.BLOCKED: "not available", readiness.WARNING: "read with care"}.get(status, "")
            item.setToolTip("\n\n".join([spec.tooltip] + reasons))
            item.setData(Qt.ItemDataRole.AccessibleTextRole,
                         f"{spec.title}, {spec.subtitle}" + (f", {words}" if words else ""))

    def changeEvent(self, event) -> None:
        from PyQt6.QtCore import QEvent

        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.PaletteChange):
            self._paint_list()
        super().changeEvent(event)

    # ------------------------------------------------------------ showing a result

    def selected_spec(self) -> Optional[workbench.Spec]:
        item = self.list.currentItem()
        key = item.data(Qt.ItemDataRole.UserRole) if item else None
        return workbench.BY_KEY.get(key) if key else None

    def select(self, key: str) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == key:
                self.list.setCurrentRow(row)
                return

    def _show_selected(self) -> None:
        spec = self.selected_spec()
        uses = spec.options if spec is not None else ()
        for widgets, option in (((self.rank_label, self.rank_picker), "rank"), ((self.contaminants,), "contaminants"),
                                ((self.seed_label, self.seed), "seed")):
            for widget in widgets:
                widget.setVisible(option in uses)
        for widget in (self.review_label, self.review_name, self.review_button):
            widget.setVisible(spec is not None and spec.needs_review)
        outcome = self._outcomes.get(spec.key) if spec else None
        self.run_button.setEnabled(spec is not None and self.run_dir() is not None and self._worker is None)
        self.save_button.setEnabled(outcome is not None and outcome.saved_to is None)
        self.open_button.setEnabled(outcome is not None and outcome.saved_to is not None)
        if self.run_dir() is None:
            self._placeholder("No finished runs in your results folder yet. Run the pipeline from the Run tab first.")
            return
        if spec is None:
            self._placeholder("Choose an analysis from the list.")
            return
        if outcome is None:
            status, reasons = self._statuses.get(spec.key, (workbench.AVAILABLE, []))
            lead = f"Press Run to compute {spec.title.lower()} for this run."
            if status == readiness.BLOCKED and reasons:
                lead = reasons[0]
            self._placeholder(lead)
            return
        self.stack.setCurrentWidget(self.result_tabs)
        self.table_picker.blockSignals(True)
        self.table_picker.clear()
        for table in outcome.tables:
            self.table_picker.addItem(table.label)
        self.table_picker.setVisible(len(outcome.tables) > 1)
        self.table_picker.blockSignals(False)
        self._show_table()
        curve = workbench.accumulation_curve(outcome)
        self.chart.set_curve(curve, self._caption(outcome))
        self.result_tabs.setTabEnabled(1, curve is not None)
        self.notes.setPlainText(outcome.notes)
        self._say(f"Saved to {outcome.saved_to}" if outcome.saved_to else "Not saved yet.")

    def _placeholder(self, text: str) -> None:
        self.placeholder.setText(text)
        self.stack.setCurrentWidget(self.placeholder)

    def _show_table(self) -> None:
        spec = self.selected_spec()
        outcome = self._outcomes.get(spec.key) if spec else None
        index = self.table_picker.currentIndex()
        if outcome is None or not outcome.tables or index < 0:
            self.model.set_table([], [])
            return
        table = outcome.tables[index]
        self.model.set_table(table.columns, table.rows)
        self.table.resizeColumnsToContents()

    @staticmethod
    def _caption(outcome: workbench.Outcome) -> str:
        return f"{outcome.context.locus}, {Path(outcome.context.run_dir).name}"

    # ------------------------------------------------------------ Run and Save

    def run_selected(self) -> None:
        spec, context = self.selected_spec(), self.context()
        if spec is None or context is None or self._worker is not None:
            return
        status, reasons = self._statuses.get(spec.key, (workbench.AVAILABLE, []))
        if status == readiness.BLOCKED:
            # Activating a blocked analysis says why (rule 9); it never fails silently.
            self._say(reasons[0] if reasons else f"{spec.title} cannot run on this run.", warning=True)
            return
        self._say(f"Running {spec.title.lower()}…")
        self.run_button.setEnabled(False)
        self._worker = AnalysisWorker(spec, context, self)
        self._worker.completed.connect(self._completed)
        self._worker.failed.connect(self._failed)
        self._worker.finished.connect(self._worker_done)
        self._worker.start()

    def _completed(self, outcome: workbench.Outcome) -> None:
        old = self._outcomes.get(outcome.spec.key)
        if old is not None and old.saved_to is None:
            workbench.discard(old)
        self._outcomes[outcome.spec.key] = outcome
        if outcome.spec.chart:
            self._chart_outcome = outcome
            self._refresh_preview()
        if self.selected_spec() is outcome.spec:
            self._show_selected()

    def _failed(self, message: str) -> None:
        self._say(f"The analysis stopped: {message}", warning=True)

    def _worker_done(self) -> None:
        self._worker = None
        self._show_selected()

    def wait_for_run(self, milliseconds: int = 60000) -> None:
        """For tests and shutdown: wait until a running analysis has finished and reported."""
        from PyQt6.QtWidgets import QApplication

        if self._worker is not None:
            self._worker.wait(milliseconds)
        QApplication.processEvents()

    def _save_clicked(self) -> None:
        spec = self.selected_spec()
        outcome = self._outcomes.get(spec.key) if spec else None
        if outcome is None:
            return
        chosen, _ = QFileDialog.getSaveFileName(self, f"Save {spec.title.lower()}", str(workbench.default_folder(outcome)),
                                                "A folder for the result (*)")
        if chosen:
            self.save_result(Path(chosen))

    def save_result(self, destination: Path) -> Optional[Path]:
        """Save the shown result to `destination` (a folder not yet made)."""
        spec = self.selected_spec()
        outcome = self._outcomes.get(spec.key) if spec else None
        if outcome is None:
            return None
        curve = workbench.accumulation_curve(outcome)
        if curve is not None and not (outcome.staged / CHART_FILE).exists():
            # Into the working folder, so Save copies it with everything else
            # and `analysis_settings.json` lists it.
            write_chart(curve, self._caption(outcome), outcome.staged / CHART_FILE)
        try:
            saved = workbench.save(outcome, destination)
        except (OSError, FileExistsError) as problem:
            self._say(f"Not saved: {problem}", warning=True)
            return None
        self._show_selected()
        return saved

    def _open_saved(self) -> None:
        spec = self.selected_spec()
        outcome = self._outcomes.get(spec.key) if spec else None
        if outcome and outcome.saved_to:
            platform_utils.open_in_file_manager(outcome.saved_to)

    # ------------------------------------------------------------ the preview and the status line

    def _refresh_preview(self) -> None:
        outcome = self._chart_outcome
        curve = workbench.accumulation_curve(outcome) if outcome else None
        self.preview.set_curve(curve, self._caption(outcome) if outcome else "")
        self.preview.setVisible(curve is not None)
        self.preview_label.setVisible(curve is not None)

    def _open_chart(self) -> None:
        if self._chart_outcome is None:
            return
        self.select(self._chart_outcome.spec.key)
        self.result_tabs.setCurrentWidget(self.chart)
        self.chart.setFocus()

    def _say(self, text: str, warning: bool = False) -> None:
        colours = theme.active()
        self.status.setText((f"{WARNING_SIGN} " if warning else "") + text)
        self.status.setStyleSheet(f"color: {colours['log_warning'] if warning else colours['muted']};")

    def shutdown(self) -> None:
        """Before the window closes: finish any running analysis and remove unsaved working folders."""
        self.wait_for_run()
        self._forget_results()
        if self._library is not None:
            self._library.close()

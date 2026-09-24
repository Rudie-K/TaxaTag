# src/gui/main_window.py
"""
The TaxaTag window.

Organised so that the first tab holds everything needed for an ordinary run -
where the data is, where results go, and a button to start - and the remaining
tabs hold the things that are set once and then left alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.gui import theme
from src.gui.primers import PrimerSetPanel, complete_locus, top_up_with_presets
from src.gui.results import ResultsPanel
from src.gui.widgets import CheckListView, HelpLabel, LogView, PathPicker
from src.gui.worker import PipelineWorker, SelfTestWorker, ValidationWorker
from src.pipeline import resume as resume_module
from src.pipeline.config import PipelineConfig
from src.utils import platform as platform_utils
from src.utils import paths
from src.version import __version__

APP_NAME = "TaxaTag"
APP_TAGLINE = "Environmental DNA to species, without the command line"

#: Copyright is held by a person, not by a project - "TaxaTag" is a name and
#: cannot own anything. Written once, here, so that a change of year or an
#: assignment to a company later is one edit.
#: All three contributed elements of the program and hold copyright in
#: it jointly. Naming one would be both discourteous and inaccurate,
#: and the GPL requires the notice to say who the holders are.
COPYRIGHT = "Copyright (C) 2026 Rudie Kauhanen, Lucy Thomas and Ruth Farrant"


class MainWindow(QMainWindow):
    def __init__(self, config_path: Optional[Path] = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1120, 780)
        self.setMinimumSize(900, 640)

        self.config_path = Path(config_path) if config_path else platform_utils.user_config_path()
        self.worker: Optional[PipelineWorker] = None
        self.validator: Optional[ValidationWorker] = None
        self.last_run_dir: Optional[Path] = None
        #: The unfinished run the Resume button would continue, if any.
        self.resume_point: Optional[resume_module.ResumePoint] = None
        self._running = False

        self._build_menu()
        self._build_body()
        self._load_settings()
        # So that "Match my computer" keeps matching it while the window
        # is open, rather than only at launch.
        QApplication.instance().styleHints().colorSchemeChanged.connect(
            self._system_scheme_changed
        )
        self._refresh_resume()
        self.results_panel.set_results_folder(self.output_picker.value())
        self.statusBar().showMessage("Ready")

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        # The underlined letter is F, not S: "Save settings" already claims S,
        # and two items sharing one key means Alt+F,S cycles between them
        # instead of opening either.
        open_action = QAction("Settings &file...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_settings)
        file_menu.addAction(open_action)

        save_action = QAction("&Save settings", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(lambda: self.save_settings(announce=True))
        file_menu.addAction(save_action)

        save_as_action = QAction("Save settings &as...", self)
        save_as_action.triggered.connect(self.save_settings_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()
        quit_action = QAction("E&xit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About TaxaTag", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def _build_body(self) -> None:
        header = QWidget()
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(14, 10, 14, 4)
        header_row.setSpacing(12)

        # The emblem, when the resources folder has one. Its absence is not
        # an error - a source checkout that has not run make_icon.py, or a
        # build made before the logo existed, gets the text header it always
        # had rather than a broken-image placeholder.
        emblem = platform_utils.app_root() / "resources" / "taxatag_emblem.png"
        if emblem.exists():
            from PyQt6.QtGui import QColor, QPixmap
            from PyQt6.QtWidgets import QGraphicsDropShadowEffect

            picture = QLabel()
            picture.setPixmap(QPixmap(str(emblem)))
            picture.setObjectName("emblem")
            picture.setFixedWidth(picture.pixmap().width())
            # A soft shadow under the rounded cream card, the way a desktop
            # icon sits on a dock. It is what turns a pasted-in picture into
            # an object on the surface. Kept faint - a shadow you notice is
            # a shadow that is wrong - and offset downwards a little, since
            # light is assumed to come from above.
            lift = QGraphicsDropShadowEffect(picture)
            lift.setBlurRadius(14)
            lift.setOffset(0, 2)
            lift.setColor(QColor(0, 0, 0, 55))
            picture.setGraphicsEffect(lift)
            # Room for the shadow to fall into, or the widget clips it.
            picture.setContentsMargins(4, 4, 6, 8)
            picture.setFixedWidth(picture.pixmap().width() + 10)
            header_row.addWidget(picture, 0, Qt.AlignmentFlag.AlignVCenter)

        words = QVBoxLayout()
        words.setSpacing(2)
        title = QLabel(APP_NAME)
        title.setObjectName("titleLabel")
        # The version beside the name, as text: the emblem is never altered
        # to carry it. Quiet, in the help text's colour, which is measured
        # against every scheme's ground. A screenshot in a methods section
        # or a problem report can then say which program made it (item 12).
        self.version_label = QLabel(f"version {__version__}")
        self.version_label.setObjectName("helpLabel")
        self.version_label.setAccessibleName(f"{APP_NAME} version {__version__}")
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.addWidget(title, 0, Qt.AlignmentFlag.AlignBottom)
        name_row.addWidget(self.version_label, 0, Qt.AlignmentFlag.AlignBottom)
        name_row.addStretch(1)
        words.addLayout(name_row)
        words.addWidget(HelpLabel(APP_TAGLINE))
        words.addStretch(1)
        header_row.addLayout(words, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_run_tab(), "Run")
        self.tabs.addTab(self._build_settings_tab(), "Settings")
        self.primer_panel = PrimerSetPanel()
        self.tabs.addTab(_padded(self.primer_panel), "Primer sets")
        self.results_panel = ResultsPanel()
        self.tabs.addTab(self.results_panel, "Results")

        # Refreshed on returning to the Run tab, which covers the changes
        # that have no signal to listen to - a primer set switched off, a
        # threshold edited. Cheaper than wiring every control on every tab,
        # and it cannot go stale, because the summary is only ever read on
        # the tab that shows it.
        self.tabs.currentChanged.connect(lambda _: self.refresh_plan())

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 0, 10, 10)
        outer.setSpacing(6)
        outer.addWidget(header)
        outer.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

    def _build_run_tab(self) -> QWidget:
        folders = QGroupBox("Your data")
        self.input_picker = PathPicker("Folder containing your FASTQ or SRA files")
        self.output_picker = PathPicker("Folder where results should be written")
        folders_form = QFormLayout(folders)
        folders_form.setSpacing(6)
        folders_form.addRow("Sequencing files", self.input_picker)
        self.input_picker.set_label("Sequencing files")
        # Also `settled`: the summary counts samples by listing the folder.
        self.input_picker.settled.connect(lambda _: self.refresh_plan())
        folders_form.addRow(
            HelpLabel(
                "Point this at the folder holding your reads. Paired files are matched "
                "up automatically, whatever they are named, and files split across "
                "lanes are joined back together."
            )
        )
        folders_form.addRow("Results folder", self.output_picker)
        self.output_picker.set_label("Results folder")
        # `settled` rather than `changed`: both reactions read the disk -
        # one looks for an unfinished run, the other reads a manifest per
        # finished one and loads a table - and `changed` fires on every
        # keystroke. See PathPicker.
        self.output_picker.settled.connect(lambda _: self._output_folder_settled())
        folders_form.addRow(
            HelpLabel(
                "Each run creates its own dated folder here, so an earlier run is "
                "never overwritten."
            )
        )

        # Optional, and appended to the timestamp rather than replacing it.
        # A folder called "Pond survey" would sort after every date, so the
        # program could no longer tell which run was the most recent - and
        # Resume would offer to continue the wrong one forever.
        self.run_name = QLineEdit()
        self.run_name.setPlaceholderText("Optional - for example: Pond A, June")
        self.run_name.textChanged.connect(lambda _: self.refresh_plan())
        folders_form.addRow("Name this run", self.run_name)
        folders_form.addRow(
            HelpLabel(
                "Added to the end of the run folder's name, after the date, so "
                "it is easy to find again. Leave it empty for just the date."
            )
        )

        self.check_button = QPushButton("&Check my setup")
        self.check_button.clicked.connect(self.run_checks)
        # A real analysis of bundled data with a known answer. Separate from
        # the checks because it takes half a minute rather than an instant,
        # and because it answers a different question: the checks say the
        # pieces are present, this says they work together.
        # "Run a self-test" said what the program was doing rather than what
        # the user was getting. This names the thing being tested, which is
        # the question somebody has when deciding whether to press it.
        self.selftest_button = QPushButton("&Test the analysis pipeline")
        self.selftest_button.setToolTip(
            "Analyse four small built-in samples whose contents are known, to "
            "confirm TaxaTag is installed and working. Takes about fifteen "
            "seconds and needs no internet connection."
        )
        self.selftest_button.clicked.connect(self.run_selftest)
        self.run_button = QPushButton("&Start analysis")
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(self.start_run)
        self.stop_button = QPushButton("Sto&p")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self.stop_run)
        self.stop_button.setVisible(False)
        # Shown only when there is genuinely something to continue, so its
        # presence is the answer to "can I pick up where I left off?" and the
        # user never has to press it to find out.
        self.resume_button = QPushButton("&Resume last run")
        self.resume_button.clicked.connect(self.resume_run)
        self.resume_button.setVisible(False)

        controls = QHBoxLayout()
        controls.addWidget(self.check_button)
        controls.addWidget(self.selftest_button)
        controls.addStretch(1)
        controls.addWidget(self.resume_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.run_button)

        # What pressing Start would actually do. The two settings that decide
        # whether a run takes seconds or hours - which markers, and what the
        # sequences are compared against - live on other tabs, so without
        # this the moment of committing to them is the moment they are least
        # visible.
        self.plan_label = QLabel("")
        self.plan_label.setObjectName("planLabel")
        self.plan_label.setWordWrap(True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.status_label = QLabel("Ready when you are.")
        self.status_label.setObjectName("statusLabel")

        self.check_view = CheckListView()
        # Four lines or so; it scrolls for more. Was 150, which on a
        # laptop screen pushed itself below the fold - the one place a
        # list of problems must not be.
        self.check_view.setMaximumHeight(110)
        self.check_view.setVisible(False)

        self.log_view = LogView()

        save_log = QPushButton("Save &log to a file...")
        save_log.clicked.connect(self.save_log)
        self.open_results_button = QPushButton("&Open results folder")
        self.open_results_button.clicked.connect(self.open_results_folder)
        self.open_results_button.setEnabled(False)

        log_buttons = QHBoxLayout()
        log_buttons.addWidget(QLabel("Progress log"))
        log_buttons.addStretch(1)
        log_buttons.addWidget(self.open_results_button)
        log_buttons.addWidget(save_log)

        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.addLayout(log_buttons)
        lower_layout.addWidget(self.log_view, 1)

        upper = QWidget()
        upper_layout = QVBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.addWidget(folders)
        # Between the folders and the buttons on purpose: it is the
        # consequence of what is above it, and the last thing read before
        # committing to what is below it.
        upper_layout.addWidget(self.plan_label)
        upper_layout.addLayout(controls)
        upper_layout.addWidget(self.progress)
        upper_layout.addWidget(self.status_label)
        upper_layout.addWidget(self.check_view)
        # Spare height goes here, not into the group box above.
        upper_layout.addStretch(1)

        # The upper half scrolls rather than clips.
        #
        # It grew - a summary bar, a run-name row, a check list - until its
        # natural height exceeded what fits on a laptop screen with the log
        # below it, and a QSplitter given less than a pane's minimum does
        # not refuse: it hands over what it has and the form rows overlap.
        # The "Your data" box was drawn at 249 pixels against a hint of 313,
        # with every field's text cut off at the top. That happened on a
        # 1007-pixel window, so raising the minimum window size is not the
        # answer - it would only lock small screens out. A scroll area makes
        # overlap impossible: on a screen that is too short, a scrollbar
        # appears, and everything stays readable.
        # No explicit height. There used to be one - the box was pinned to
        # `sizeHint().height()` because a form of word-wrapped labels claimed
        # it could shrink to nothing and its rows then overlapped. Both the
        # claim and the pin were wrong, and for one reason: `HelpLabel` was
        # denying that its height depends on its width, so every layout
        # above it guessed. It no longer denies it (see `HelpLabel`), and Qt
        # sizes this box correctly on its own - measured at 213 pixels
        # against the pinned 297, with nothing clipped even when the pane is
        # squeezed to 90. `Fixed` only keeps spare height going to the
        # stretch below rather than into this box.
        folders.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        upper_scroll = QScrollArea()
        upper_scroll.setWidgetResizable(True)
        upper_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        upper_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        upper_scroll.setWidget(upper)
        # Ask for the whole thing when there is room; the scrollbar is for
        # when there is not.
        upper_scroll.setMinimumHeight(160)

        # The log can be short. It is a stream a user scrolls anyway, and
        # taking space from it is far better than clipping the controls.
        lower.setMinimumHeight(80)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(upper_scroll)
        splitter.addWidget(lower)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        # Start with the upper pane at its natural height, so the scrollbar
        # only ever appears when the window genuinely cannot hold it.
        splitter.setSizes([upper.sizeHint().height(), 200])
        # Kept so that a change of text size can ask again: the natural
        # height taken here is the one at the size the window opened with.
        self.run_splitter, self.run_upper = splitter, upper

        return _padded(splitter)

    def _resettle_run_tab(self) -> None:
        """Give the upper pane its natural height at the current text size."""
        QApplication.instance().processEvents()      # labels re-measure first
        wanted = self.run_upper.sizeHint().height()
        total = sum(self.run_splitter.sizes())
        self.run_splitter.setSizes([wanted, max(80, total - wanted)])

    def _build_display_group(self) -> QGroupBox:
        """
        How the window looks. First on the page, before the science, because
        somebody who needs a darker screen needs it before they can read
        the rest of the page.

        Not part of the settings file: it is kept per user, not per project,
        and `apply_config` / `current_config` do not know it exists.
        """
        display = QGroupBox("Display")
        self.scheme_choice = QComboBox()
        for label, choice in (
            ("Match my computer", theme.SYSTEM),
            ("Light", theme.LIGHT),
            ("Dark", theme.DARK),
            ("High contrast", theme.HIGH_CONTRAST),
        ):
            self.scheme_choice.addItem(label, choice)
        self.scheme_choice.setCurrentIndex(
            max(0, self.scheme_choice.findData(theme.saved_choice()))
        )
        self.scheme_choice.currentIndexChanged.connect(self._display_changed)

        self.size_choice = QComboBox()
        for label, size in (
            ("Small", theme.SMALL),
            ("Normal", theme.NORMAL),
            ("Large", theme.LARGE),
        ):
            self.size_choice.addItem(label, size)
        self.size_choice.setCurrentIndex(
            max(0, self.size_choice.findData(theme.saved_size()))
        )
        self.size_choice.currentIndexChanged.connect(self._display_changed)

        self.dyslexic_font = QCheckBox("Use a dyslexia-friendly font")
        self.dyslexic_font.setChecked(theme.saved_dyslexic())
        self.dyslexic_font.toggled.connect(self._display_changed)

        form = QFormLayout(display)
        form.addRow("Colour scheme", self.scheme_choice)
        form.addRow(
            HelpLabel(
                "Changes straight away. \"Match my computer\" follows the setting "
                "in Windows or macOS, so the window goes dark when the rest of "
                "the desktop does."
            )
        )
        form.addRow("Text size", self.size_choice)
        form.addRow(self.dyslexic_font)
        form.addRow(
            HelpLabel(
                "OpenDyslexic: letters weighted at the bottom and shaped so that "
                "b, d, p and q are harder to confuse. Some people read it more "
                "easily, some do not; it is here to try."
            )
        )
        return display

    def _display_changed(self, *_args) -> None:
        theme.save_choice(self.scheme_choice.currentData())
        theme.save_size(self.size_choice.currentData())
        theme.save_dyslexic(self.dyslexic_font.isChecked())
        self._apply_scheme()

    def _apply_scheme(self) -> None:
        """Put the chosen scheme, size and face on screen, including what
        the stylesheet cannot reach: lines already in the log and check
        list."""
        app = QApplication.instance()
        theme.apply(
            app,
            theme.resolve(theme.saved_choice()),
            size=theme.saved_size(),
            dyslexic=theme.saved_dyslexic(),
        )
        self.log_view.repaint_lines()
        self.check_view.repaint_lines()
        self._resettle_run_tab()

    def _system_scheme_changed(self, _scheme) -> None:
        # Only matters when following the system; a fixed choice ignores it.
        if theme.saved_choice() == theme.SYSTEM:
            self._apply_scheme()

    def _build_settings_tab(self) -> QWidget:
        display = self._build_display_group()

        # --- reading the data -------------------------------------------
        reading = QGroupBox("Reading your files")
        self.convert_sra = QCheckBox("Convert SRA files downloaded from NCBI")
        self.merge_reads = QCheckBox("Join forward and reverse reads together")
        self.merge_overlap = QSpinBox()
        self.merge_overlap.setRange(4, 200)
        self.merge_overlap.setSuffix(" bases")
        self.merge_reads.toggled.connect(self.merge_overlap.setEnabled)

        reading_form = QFormLayout(reading)
        reading_form.addRow(self.convert_sra)
        reading_form.addRow(
            HelpLabel("Leave this on unless your data is already in FASTQ format.")
        )
        reading_form.addRow(self.merge_reads)
        reading_form.addRow(
            HelpLabel(
                "Joining the two reads of a pair gives one sequence covering the whole "
                "marker, which identifies species more reliably. Turn it off only to "
                "match an older analysis that used the forward read on its own."
            )
        )
        reading_form.addRow("Smallest overlap to join on", self.merge_overlap)

        # --- finding real sequences -------------------------------------
        processing = QGroupBox("Finding real sequences")
        self.min_zotu_size = QSpinBox()
        self.min_zotu_size.setRange(1, 100000)
        self.min_zotu_size.setSuffix(" reads")
        self.apply_length_filter = QCheckBox(
            "Discard sequences outside the expected length for their primer set"
        )
        self.threads = QSpinBox()
        self.threads.setRange(0, 256)
        self.threads.setSpecialValueText("Use every processor core")

        processing_form = QFormLayout(processing)
        processing_form.addRow("Minimum times a sequence must be seen", self.min_zotu_size)
        processing_form.addRow(
            HelpLabel(
                "Sequences seen fewer times than this are treated as sequencing error "
                "rather than something really present. Raising it is stricter."
            )
        )
        processing_form.addRow(self.apply_length_filter)
        processing_form.addRow(
            HelpLabel(
                "Off by default, because the expected length usually quoted for a "
                "primer set includes the primers themselves and so does not match what "
                "survives trimming. The report after each run shows the lengths "
                "actually seen, so you can check before switching this on."
            )
        )
        processing_form.addRow("Processor cores to use", self.threads)

        # --- identification ---------------------------------------------
        identification = QGroupBox("Identifying species")
        self.blast_mode = QComboBox()
        self.blast_mode.addItem("Use a TaxaTag reference library", "reference")
        self.blast_mode.addItem("Search NCBI over the internet", "remote")
        self.blast_mode.addItem("Search another database on this computer", "local")
        self.blast_mode.currentIndexChanged.connect(self._update_blast_mode)

        # A list rather than a folder box. Someone who installed TaxaTag and
        # built a library should not have to remember where it went, and the
        # folder it lives in is not a thing they chose or need to know. The
        # last entry still opens a folder box, for a library kept elsewhere.
        self.reference_library = QComboBox()
        self.reference_library.setMinimumWidth(320)
        self.reference_library.activated.connect(self._library_chosen)
        self.library_status = HelpLabel("")

        self.blast_db = PathPicker(
            "Database name, without a file extension", mode="open",
            file_filter="BLAST database (*.nin *.nal *.ndb);;All files (*)",
        )

        self.min_coverage = QDoubleSpinBox()
        self.min_coverage.setRange(0.0, 100.0)
        self.min_coverage.setDecimals(0)
        self.min_coverage.setSuffix(" % of the sequence")
        self.ncbi_email = QLineEdit()
        self.ncbi_email.setPlaceholderText("you@university.ac.uk")

        self.identity_species = _percent_spin()
        self.identity_genus = _percent_spin()
        self.identity_family = _percent_spin()

        identification_form = QFormLayout(identification)
        identification_form.addRow("Where to search", self.blast_mode)
        identification_form.addRow(
            HelpLabel(
                "A reference library on this computer is by far the fastest option, "
                "works with no internet connection, and gives the same answer every "
                "time. Searching NCBI needs no setup but is slow and depends on how "
                "busy their servers are."
            )
        )
        identification_form.addRow("Reference library", self.reference_library)
        identification_form.addRow("", self.library_status)
        identification_form.addRow("Other BLAST database", self.blast_db)
        identification_form.addRow("Your email address", self.ncbi_email)
        identification_form.addRow(
            HelpLabel(
                "NCBI ask for this so they can contact you if a search causes them "
                "trouble. It is sent only to NCBI."
            )
        )
        identification_form.addRow("Name to species when match is at least", self.identity_species)
        identification_form.addRow("Name to genus when match is at least", self.identity_genus)
        identification_form.addRow("Name to family when match is at least", self.identity_family)
        identification_form.addRow(
            HelpLabel(
                "How close a match has to be before a name is trusted. Anything below "
                "the family figure is reported as unidentified rather than guessed at."
            )
        )
        identification_form.addRow("Match must cover at least", self.min_coverage)
        identification_form.addRow(
            HelpLabel(
                "How much of your sequence has to take part in the match. This matters "
                "as much as the percentage above: a fragment matching 55 bases of a "
                "250-base sequence perfectly is reported by BLAST as a 100% match, and "
                "without this would be recorded as a confident species identification."
            )
        )

        # --- housekeeping ------------------------------------------------
        housekeeping = QGroupBox("Housekeeping")
        self.clean_output = QCheckBox("Delete previous runs before starting a new one")
        housekeeping_form = QFormLayout(housekeeping)
        housekeeping_form.addRow(self.clean_output)
        housekeeping_form.addRow(
            HelpLabel(
                "Off by default. Every run normally goes into its own dated folder so "
                "nothing is ever lost."
            )
        )

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(10)
        for group in (display, reading, processing, identification, housekeeping):
            content_layout.addWidget(group)
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return _padded(scroll)

    # ------------------------------------------------------------------
    # Settings <-> widgets
    # ------------------------------------------------------------------
    def _load_settings(self) -> None:
        """Load the user's saved settings, or sensible defaults on first run."""
        source = self.config_path
        if not paths.exists(source):
            bundled = platform_utils.bundled_default_config()
            if bundled:
                source = bundled

        if paths.exists(source):
            try:
                config = PipelineConfig.from_yaml(source)
            except Exception as error:  # noqa: BLE001 - fall back rather than fail to start
                QMessageBox.warning(
                    self,
                    "Settings could not be read",
                    f"{source}\n\n{error}\n\nStarting with default settings instead.",
                )
                config = PipelineConfig()
        else:
            config = PipelineConfig()

        # A first run should not inherit the placeholder paths from the example
        # settings file, which point at folders that do not exist. Compared as
        # posix strings because Path renders these with backslashes on Windows.
        placeholder_input = {".", "", "path/to/input"}
        placeholder_output = {".", "", "./output", "output", "path/to/output"}

        # paths.exists, not Path.exists: this runs during start-up on a
        # folder the user chose and saved, which is exactly where two
        # earlier crashes came from on the library folder beside it.
        if Path(config.input_dir).as_posix() in placeholder_input or not paths.exists(
            config.input_dir
        ):
            config.input_dir = Path("")
        if Path(config.output_base).as_posix() in placeholder_output:
            config.output_base = Path.home() / "TaxaTag Results"

        if not config.loci:
            # A new installation: the four defaults on, one per marker.
            from src.gui.primers import PRESETS

            config.loci = [complete_locus(preset) for preset in PRESETS]
        else:
            # An existing settings file: it keeps every tick it had, and gains
            # any standard set it did not know about, switched off. Otherwise
            # a file written before a preset existed can only reach it through
            # a menu item the user has to know to look for - and the symptom
            # of not finding it is a sample reported as having no recognisable
            # primers.
            config.loci = top_up_with_presets(config.loci)

        self.apply_config(config)

    def apply_config(self, config: PipelineConfig) -> None:
        """Push a configuration into the widgets."""
        self.run_name.setText(config.run_name)
        self.input_picker.set_value(config.input_dir if str(config.input_dir) != "." else "")
        self.output_picker.set_value(config.output_base)

        self.convert_sra.setChecked(config.convert_sra)
        self.merge_reads.setChecked(config.merge_reads)
        self.merge_overlap.setValue(config.min_merge_overlap)
        self.merge_overlap.setEnabled(config.merge_reads)

        self.min_zotu_size.setValue(config.min_zotu_size)
        self.apply_length_filter.setChecked(config.apply_length_filter)
        self.threads.setValue(config.threads)

        index = self.blast_mode.findData(config.blast_mode)
        self.blast_mode.setCurrentIndex(index if index >= 0 else 0)
        self.blast_db.set_value(config.blast_db)
        self._fill_library_list(keep=str(config.reference_dir or ""))
        self.min_coverage.setValue(float(config.min_query_coverage))
        self.ncbi_email.setText(config.ncbi_email)

        self.identity_species.setValue(float(config.min_identity.get("species", 99.0)))
        self.identity_genus.setValue(float(config.min_identity.get("genus", 97.0)))
        self.identity_family.setValue(float(config.min_identity.get("family", 95.0)))

        self.clean_output.setChecked(config.clean_output)
        self.primer_panel.set_loci(config.loci)
        self._update_blast_mode()

    def current_config(self) -> PipelineConfig:
        """Build a configuration from what is currently on screen."""
        config = PipelineConfig(
            run_name=self.run_name.text().strip(),
            input_dir=Path(self.input_picker.value() or "."),
            output_base=Path(self.output_picker.value() or "./output"),
            convert_sra=self.convert_sra.isChecked(),
            merge_reads=self.merge_reads.isChecked(),
            min_merge_overlap=self.merge_overlap.value(),
            min_zotu_size=self.min_zotu_size.value(),
            apply_length_filter=self.apply_length_filter.isChecked(),
            threads=self.threads.value(),
            blast_mode=self.blast_mode.currentData(),
            blast_db=Path(self.blast_db.value()) if self.blast_db.value() else None,
            reference_dir=(
                Path(self.selected_library()) if self.selected_library() else None
            ),
            min_query_coverage=self.min_coverage.value(),
            ncbi_email=self.ncbi_email.text().strip(),
            clean_output=self.clean_output.isChecked(),
            min_identity={
                "species": self.identity_species.value(),
                "genus": self.identity_genus.value(),
                "family": self.identity_family.value(),
            },
            loci=self.primer_panel.loci(),
            taxonomy_cache=platform_utils.default_cache_path(),
        )
        return config

    def _output_folder_settled(self) -> None:
        """
        React to the results folder once the user has stopped typing.

        Kept together because they read the same folder and neither is worth
        doing twice.
        """
        self._refresh_resume()
        self.results_panel.set_results_folder(self.output_picker.value())

    def refresh_plan(self) -> None:
        """
        Say what pressing Start would do, from the settings as they stand.

        Called whenever anything that changes the answer changes: the input
        folder, the identification mode, the library, the primer sets. It
        reads only file *names* and one catalogue count, so it is cheap
        enough to run on every edit - and it must be, because a summary that
        lags behind the controls above it is worse than none.
        """
        from src.utils.validate import plan_run

        try:
            plan = plan_run(self.current_config())
        except Exception as problem:  # noqa: BLE001 - a summary must never stop the window
            # Blank for the user, because a broken summary is not their
            # problem and a stack trace above the Start button helps nobody.
            #
            # But not silent. Swallowing this without trace is how a feature
            # that stopped working in a packaged build goes unnoticed for a
            # release: it simply shows nothing, which is also what it shows
            # when there is nothing to say. The log is where the difference
            # can be seen by whoever goes looking.
            self.plan_label.setText("")
            self.log_view.append_line(
                f"The run summary could not be worked out: {problem}", "debug"
            )
            return

        self.plan_label.setText(plan.describe())

    def _update_blast_mode(self) -> None:
        mode = self.blast_mode.currentData()
        self.blast_db.setEnabled(mode == "local")
        self.reference_library.setEnabled(mode == "reference")
        self.library_status.setVisible(mode == "reference")
        if mode == "reference":
            self._describe_library()
        self.refresh_plan()

    #: Marks the entry that opens a folder box instead of choosing a library.
    BROWSE_FOR_LIBRARY = "__browse__"

    def selected_library(self) -> str:
        """The folder of the chosen reference library, or "" if none is."""
        from src.gui.library_download import DOWNLOAD_A_LIBRARY

        value = self.reference_library.currentData()
        # Two of the entries are actions rather than libraries. Letting
        # either through would hand the pipeline a folder name that is not
        # one and fail somewhere much less obvious.
        if value in (None, self.BROWSE_FOR_LIBRARY, DOWNLOAD_A_LIBRARY):
            return ""
        return str(value)

    def _fill_library_list(self, keep: str = "") -> None:
        """
        List the reference libraries on this machine, keeping the chosen one.

        `keep` is included even when it is nowhere TaxaTag would have looked,
        so a library the user picked by hand does not vanish from the list the
        next time the window opens.
        """
        from src.reference import library as library_module

        self.reference_library.blockSignals(True)
        self.reference_library.clear()

        found = library_module.discover(extra=[Path(keep)] if keep else [])
        for library in found:
            self.reference_library.addItem(
                f"{library.name}  -  {library.describe()}", str(library.root)
            )
            library.close()

        if not found:
            # Naming the next step in the item itself, because this is the one
            # case where the list is not a list of choices and a user could
            # reasonably conclude the feature is broken rather than empty.
            self.reference_library.addItem(
                "No library found - use 'Choose a folder...' below", ""
            )

        # The offer, kept in the list permanently rather than shown once at
        # first run and never again. Greyed so it reads as secondary to the
        # libraries actually present, and selectable anyway: it is a way
        # back, not a label. Absent entirely until a library is published,
        # because an entry with no checksum is not offerable.
        from src.gui import library_download
        from src.reference import download as download_module

        for entry in download_module.offerable():
            if download_module.installed_at(entry):
                continue          # already here, so the offer is noise
            self.reference_library.addItem(
                library_download.label_for(entry),
                library_download.DOWNLOAD_A_LIBRARY,
            )
            library_download.grey_out(
                self.reference_library, self.reference_library.count() - 1
            )

        self.reference_library.addItem("Choose a folder...", self.BROWSE_FOR_LIBRARY)

        # A library the user chose that discovery could not confirm keeps its
        # place in the list rather than being dropped.
        #
        # Falling back to whatever happened to be first is worse than it
        # sounds. It is not a tidy default: it is a *different set of
        # reference sequences*, silently substituted for the one that was
        # asked for, and saved over the setting on the way out. A run then
        # succeeds, produces a full results table, and answers a question
        # nobody asked - which is the one kind of wrong this program must
        # not be, because nothing downstream looks unusual.
        #
        # An unreadable folder is also often temporary: a drive not plugged
        # in, a network share not mounted, a policy that will be relaxed.
        # Discarding the choice because of a condition that may not outlast
        # the afternoon is not the program's decision to take.
        if keep:
            # `safe_resolve` rather than `Path.resolve`, which raises on a
            # path the operating system will not follow. This line ran only
            # when the saved library was *not* in the list - the exact case
            # a folder Windows refuses produces - so guarding discovery
            # without guarding this moved the crash three lines down.
            wanted = self.reference_library.findData(str(Path(keep)))
            if wanted < 0:
                wanted = self.reference_library.findData(str(library_module.safe_resolve(keep)))
            if wanted < 0:
                self.reference_library.insertItem(
                    0, f"{Path(keep).name}  -  chosen, but cannot be read just now",
                    str(Path(keep)),
                )
                wanted = 0
            self.reference_library.setCurrentIndex(wanted)
        self.reference_library.blockSignals(False)
        self._describe_library()
        self.refresh_plan()

        # Last, and on a thread of its own. An update check is the least
        # important thing happening at startup and the only one that waits on
        # a network, so it must not be between the user and their window.
        # The worker is kept because a QThread whose last Python reference
        # goes away is destroyed while running, which Qt answers by killing
        # the program.
        from src.gui import update_offer

        self._update_check = update_offer.check_in_background(self)

    def _library_chosen(self, index: int) -> None:
        """React to the list, which may be a request to go looking instead."""
        from src.gui import library_download

        chosen_data = self.reference_library.itemData(index)

        if chosen_data == library_download.DOWNLOAD_A_LIBRARY:
            # Selecting the offer asks again rather than starting anything.
            # A two-gigabyte download begun by a stray click on a dropdown
            # would be indefensible.
            installed = library_download.offer(self)
            self._fill_library_list(keep=installed or self.selected_library())
            return

        if chosen_data != self.BROWSE_FOR_LIBRARY:
            self._describe_library()
            return

        chosen = QFileDialog.getExistingDirectory(
            self, "Choose a reference library folder", str(Path.home())
        )
        # Cancelling must not leave "Choose a folder..." selected, because that
        # is not a library and the run would fail with a confusing message.
        self._fill_library_list(keep=chosen or self.selected_library())

    def _describe_library(self) -> None:
        """Say what is in the chosen library, in terms of markers and counts."""
        folder = self.selected_library()
        if not folder:
            # Saying where it looked turns "there isn't one" into something a
            # user can act on: either they put a library in one of these, or
            # they know theirs is somewhere else and reach for the folder box.
            from src.reference import library as library_module

            looked = ";  ".join(str(root) for root in library_module.search_roots())
            self.library_status.setText(
                f"No reference library found. TaxaTag looked in:  {looked}  -  put "
                "one in either folder and it will appear here, or use "
                "'Choose a folder...' to point at one kept elsewhere. A library "
                "is any folder containing taxatag_reference_core.db."
            )
            return

        from src.reference.library import ReferenceLibrary

        from src.reference import library as library_module

        # Asked before "is it a library?", because a folder nothing may look
        # inside answers no to that question too, and the advice that
        # follows - "it should contain taxatag_reference_core.db" - would
        # send somebody checking a folder that is correct.
        refused = library_module.cannot_be_read(folder)
        if refused:
            self.library_status.setText(
                refused + "  Your choice has been kept, so it will work again "
                "as soon as the folder can be reached."
            )
            return

        library = ReferenceLibrary(Path(folder))
        if not library.exists:
            self.library_status.setText(
                "That folder is not a reference library. It should contain "
                "taxatag_reference_core.db and a blast_volumes folder."
            )
            return
        summary = library.describe()
        library.close()
        self.library_status.setText(f"Ready: {summary} reference sequences.")

    # ------------------------------------------------------------------
    # Settings files
    # ------------------------------------------------------------------
    def save_settings(self, announce: bool = False, path: Optional[Path] = None) -> bool:
        target = Path(path) if path else self.config_path
        try:
            self.current_config().to_yaml(target)
        except OSError as error:
            QMessageBox.critical(self, "Settings not saved", f"{target}\n\n{error}")
            return False
        self.config_path = target
        if announce:
            self.statusBar().showMessage(f"Settings saved to {target}", 5000)
        return True

    def save_settings_as(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save settings", str(self.config_path), "Settings files (*.yaml *.yml)"
        )
        if chosen:
            self.save_settings(announce=True, path=Path(chosen))

    def open_settings(self) -> None:
        """
        Show the settings file as a form.

        This used to be a file-open box, which handed the user a .yaml and
        left them to it. Everything in the file is now shown and editable
        here, including the settings the Settings tab does not carry, so
        nobody has to open the file in a text editor to change one number.
        """
        from src.gui.settings_file import SettingsFileDialog

        dialog = SettingsFileDialog(self.config_path, self)
        if dialog.exec() != QDialog.DialogCode.Accepted.value:
            return

        if dialog.path:
            self.config_path = Path(dialog.path)
        try:
            config = PipelineConfig.from_dict(dialog.result_data)
        except Exception as error:  # noqa: BLE001 - the file itself is saved
            QMessageBox.warning(
                self, "Settings saved, but not applied",
                f"{error}\n\nThe window is still using the previous settings.",
            )
            return
        self.apply_config(config)
        self.statusBar().showMessage(f"Settings applied from {self.config_path}", 5000)

    # ------------------------------------------------------------------
    # Checking and running
    # ------------------------------------------------------------------
    def run_checks(self, then_run: bool = False, resume_point=None) -> None:
        self.check_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.status_label.setText("Checking your setup...")

        self._pending_run = then_run
        # A resumed run is checked like any other. That is the point: the
        # usual reason a run needs resuming is that a path or a database was
        # wrong, and the checks are where a user finds out it still is.
        self._pending_resume = resume_point
        self.validator = ValidationWorker(self.current_config())
        self.validator.completed.connect(self._checks_done)
        self.validator.start()

    def _checks_done(self, report) -> None:
        self.check_button.setEnabled(True)
        self.run_button.setEnabled(True)
        self.check_view.setVisible(True)
        self.check_view.show_report(report)
        self.status_label.setText(report.summary)

        if not report.can_run:
            self._pending_run = False
            self.tabs.setCurrentIndex(0)
            return

        if getattr(self, "_pending_run", False):
            self._pending_run = False
            point = getattr(self, "_pending_resume", None)
            self._pending_resume = None
            self._begin_run(point)

    def run_selftest(self) -> None:
        """Analyse the bundled samples and say whether the answer came back right."""
        if self._running:
            return
        if not self.output_picker.value():
            QMessageBox.information(
                self, "Choose a results folder",
                "Pick a folder for TaxaTag to write results into. The self-test "
                "puts its own folder there and cleans up the previous one.",
            )
            return

        self.selftest_button.setEnabled(False)
        self.check_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.status_label.setText("Testing the analysis pipeline...")
        self.log_view.clear()
        self.progress.setRange(0, 0)          # busy: it does not report progress
        # The log and the check panel are both on the Run tab, so a user who
        # started this from the Settings tab is shown what is happening.
        self.tabs.setCurrentIndex(0)

        self.selftester = SelfTestWorker(self.current_config())
        self.selftester.message.connect(self.log_view.append_line)
        self.selftester.completed.connect(self._selftest_done)
        self.selftester.start()

    def _selftest_done(self, report) -> None:
        self.selftest_button.setEnabled(True)
        self.check_button.setEnabled(True)
        self.run_button.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if report.can_run else 0)
        self.check_view.setVisible(True)
        self.check_view.show_report(report)

        # A verdict at the end of the log, in the place the user has been
        # watching for the last fifteen seconds. The check list above says
        # which parts passed; this says whether to trust the program, which
        # is the question that was actually asked - and it is the last line
        # rather than a status bar because somebody who looked away while it
        # ran comes back to the bottom of the log.
        if report.can_run:
            self.log_view.append_line(
                "Test successful - TaxaTag is installed correctly and "
                "produced the expected results.", "success",
            )
            self.status_label.setText("Test successful.")
        else:
            self.log_view.append_line(
                "Test failed - see the list above for which part could not "
                "be confirmed.", "error",
            )
            self.status_label.setText("The test found a problem.")
        self.tabs.setCurrentIndex(0)

    def start_run(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if not self.input_picker.value():
            QMessageBox.information(
                self, "Choose your files",
                "Pick the folder that holds your sequencing files first.",
            )
            return
        if not self.output_picker.value():
            QMessageBox.information(
                self, "Choose a results folder",
                "Pick a folder for TaxaTag to write results into.",
            )
            return
        # Checks first, then the run starts by itself if everything passes.
        self.run_checks(then_run=True)

    def _begin_run(self, resume_point=None) -> None:
        self.save_settings()
        config = self.current_config()

        self.log_view.clear()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.open_results_button.setEnabled(False)
        self.run_button.setVisible(False)
        self.resume_button.setVisible(False)
        self.stop_button.setVisible(True)
        self.check_button.setEnabled(False)
        self.selftest_button.setEnabled(False)
        self._lock_settings(True)
        self.statusBar().showMessage("Running")
        self._running = True

        if resume_point is not None:
            self.status_label.setText(f"Resuming at {resume_point.next_title}...")
            stages = resume_point.remaining
            run_dir = resume_point.run_dir
        else:
            self.status_label.setText("Starting...")
            stages = None
            run_dir = None

        self.worker = PipelineWorker(config, stages=stages, run_dir=run_dir)
        self.worker.message.connect(self.log_view.append_line)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_run.connect(self._on_finished)
        self.worker.start()

    def stop_run(self) -> None:
        if not (self.worker and self.worker.isRunning()):
            return
        confirm = QMessageBox.question(
            self,
            "Stop the analysis?",
            "Everything finished so far will be kept in the results folder.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.stop_button.setEnabled(False)
        self.status_label.setText("Stopping after the current step...")
        self.worker.stop()

    def _refresh_resume(self) -> None:
        """
        Decide whether there is an unfinished run to offer, and show or hide
        the button accordingly.

        The button's presence is the answer to "can I pick up where I left
        off?", so a user never has to press it to find out. Called whenever
        that answer could have changed: at startup, when the results folder
        changes, and when a run ends.
        """
        self.resume_point = None
        if self._running or not self.output_picker.value():
            self.resume_button.setVisible(False)
            return
        try:
            self.resume_point = resume_module.find_resumable(self.current_config())
        except Exception:  # noqa: BLE001 - an odd folder must not break the window
            self.resume_point = None

        point = self.resume_point
        self.resume_button.setVisible(point is not None)
        if point is not None:
            self.resume_button.setToolTip(
                f"Continue the run from {point.started}, which stopped before "
                f"it finished. It would start again at {point.next_title}, "
                f"keeping the {len(point.completed)} stage(s) already done."
            )

    #: Tabs holding settings a run reads when it starts. Locked while one is
    #: going, because changing them then does nothing - the configuration was
    #: taken at the start - and a user has no way to know that. An edit that
    #: silently applies to the next run instead of this one is worse than one
    #: that is refused.
    SETTINGS_TABS = (1, 2)

    def _lock_settings(self, locked: bool) -> None:
        """Stop settings being edited while a run is using them."""
        for index in self.SETTINGS_TABS:
            self.tabs.setTabEnabled(index, not locked)
            self.tabs.setTabToolTip(
                index,
                "Settings cannot be changed while an analysis is running. "
                "Stop the run first, or wait for it to finish." if locked else "",
            )
        if locked and self.tabs.currentIndex() in self.SETTINGS_TABS:
            self.tabs.setCurrentIndex(0)

    def resume_run(self) -> None:
        """Continue the last unfinished run, after saying what that means."""
        if self._running:
            return
        # Re-checked rather than trusted: the folder may have been emptied, or
        # the run finished in another window, since the button appeared.
        self._refresh_resume()
        point = self.resume_point
        if point is None:
            QMessageBox.information(
                self, "Nothing to resume",
                "There is no unfinished run in this results folder. "
                "Start a new analysis instead.",
            )
            return

        confirm = QMessageBox(self)
        confirm.setWindowTitle("Resume the last run?")
        # A settings change no longer makes resuming dangerous - the stages it
        # affected have already been pushed back into the work still to do -
        # but it does mean the run will redo more than the user may expect, so
        # the dialog says so and does not default to Yes.
        confirm.setIcon(
            QMessageBox.Icon.Warning if point.redo_from else QMessageBox.Icon.Question
        )
        confirm.setText(f"Carry on from {point.next_title}?")
        confirm.setInformativeText(point.summary())
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        confirm.setDefaultButton(
            QMessageBox.StandardButton.No if point.redo_from
            else QMessageBox.StandardButton.Yes
        )
        if confirm.exec() != QMessageBox.StandardButton.Yes.value:
            return

        self.run_checks(then_run=True, resume_point=point)

    def _on_progress(self, percent: int, message: str) -> None:
        if percent < 0:
            # A step whose length cannot be predicted: show a moving bar
            # rather than a percentage that would be a lie.
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
        if message:
            self.status_label.setText(message)

    def _on_finished(self, result: dict) -> None:
        self._running = False
        self._lock_settings(False)
        self.selftest_button.setEnabled(True)
        self.run_button.setVisible(True)
        self.run_button.setEnabled(True)
        self.stop_button.setVisible(False)
        self.stop_button.setEnabled(True)
        self.check_button.setEnabled(True)
        self.progress.setRange(0, 100)

        self.last_run_dir = result.get("run_dir")
        self.open_results_button.setEnabled(bool(self.last_run_dir))
        self._refresh_resume()

        status = result.get("status")
        if status == "ok":
            self.progress.setValue(100)
            self.status_label.setText(result.get("message", "Finished."))
            self.statusBar().showMessage("Finished")
            self._show_results(result)
            QMessageBox.information(
                self, "Analysis complete",
                f"{result.get('message', 'Finished.')}\n\nResults are in:\n{self.last_run_dir}",
            )
        elif status == "partial":
            self.progress.setValue(100)
            self.status_label.setText(result.get("message", "Finished with warnings."))
            self.statusBar().showMessage("Finished with warnings")
            self._show_results(result)
            QMessageBox.warning(
                self, "Finished, with some warnings",
                f"{result.get('message', '')}\n\nCheck the log for the details.\n\n"
                f"Results are in:\n{self.last_run_dir}",
            )
        elif status == "cancelled":
            self.progress.setValue(0)
            self.statusBar().showMessage("Stopped")
            if self.resume_point:
                self.status_label.setText(
                    "Stopped. Press Resume to carry on from "
                    f"{self.resume_point.next_title}."
                )
            else:
                self.status_label.setText("Stopped.")
        else:
            self.progress.setValue(0)
            self.status_label.setText("The run did not finish.")
            self.statusBar().showMessage("Problem")
            stage = result.get("stage_title", "")
            QMessageBox.critical(
                self, "The analysis could not finish",
                (f"{stage}\n\n" if stage else "") + result.get("message", "Unknown problem."),
            )

    def _show_results(self, result: dict) -> None:
        table = result.get("final_table")
        if table and paths.exists(table):
            self.results_panel.set_results_folder(self.output_picker.value())
            self.results_panel.load(Path(table), Path(result["run_dir"]))
            self.tabs.setCurrentIndex(self.tabs.count() - 1)

    # ------------------------------------------------------------------
    # Odds and ends
    # ------------------------------------------------------------------
    def open_results_folder(self) -> None:
        if self.last_run_dir and not platform_utils.open_in_file_manager(Path(self.last_run_dir)):
            QMessageBox.information(
                self, "Results folder", f"Your results are in:\n{self.last_run_dir}"
            )

    def save_log(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Save log", str(Path.home() / "taxatag_log.txt"), "Text files (*.txt)"
        )
        if chosen:
            try:
                self.log_view.save_to(Path(chosen))
                self.statusBar().showMessage(f"Log saved to {chosen}", 5000)
            except OSError as error:
                QMessageBox.critical(self, "Log not saved", str(error))

    def show_about(self) -> None:
        QMessageBox.about(self, f"About {APP_NAME}", self.about_text())

    def about_text(self) -> str:
        """Help > About, as rich text. A method so that it can be read without opening a dialog."""
        provenance = platform_utils.build_provenance()
        return (
            f"<h3>{APP_NAME}</h3>"
            f"<p>Version {__version__}" + (f" ({provenance})" if provenance else "") + "</p>"
            f"<p>{APP_TAGLINE}</p>"
            "<p>Turns environmental DNA sequencing files into a table of the species "
            "present in each sample, on Windows, macOS and Linux.</p>"
            "<p>Built on Cutadapt, VSEARCH, NCBI BLAST and the NCBI Taxonomy database. "
            "Please cite those tools alongside TaxaTag when you publish.</p>"
            # The licence belongs where a user will find it. Someone who has
            # only the built application has no README to read, and the whole
            # point of the GPL is that they can get the source - which they
            # cannot do if nothing tells them they are entitled to it.
            f"<p>{COPYRIGHT}<br>"
            "Free software under the GNU General Public License, version 3. "
            "It comes with no warranty, to the extent the law allows. You may "
            "redistribute it under the terms of that licence, and the source "
            "is included with every copy.</p>"
            # The carve-out belongs here for the same reason the licence
            # does: somebody holding only the built application has no
            # README, and "you may redistribute this" read alone says
            # something broader than what is meant.
            "<p>The name TaxaTag and its logo are not covered by that "
            "licence: all rights in them are reserved. Use them freely "
            "to refer to this program - cite it, show it, teach with "
            "it - but give a modified version a name of its own.</p>"
            f"<p style='color:{theme.active()['muted']};'>Settings file:<br>{self.config_path}</p>"
        )

    #: Remembers that the offer has been made, so it is made once.
    OFFER_SHOWN_SETTING = "library_offer_shown"

    def offer_a_library_if_there_is_none(self) -> None:
        """
        On a first run with no library, say that one can be downloaded.

        Once only. TaxaTag works without a library - it can search NCBI over
        the web - so somebody who declined has a working program and does
        not need telling again at every launch. The offer stays reachable in
        the library list, which is where they will look when the web search
        turns out to be slow.

        The flag is written whether or not they accept, because the point is
        that they have now been told. Asking a second time is nagging, and
        the entry in the list is the standing reminder.
        """
        from PyQt6.QtCore import QSettings

        from src.gui import library_download
        from src.reference import download as download_module
        from src.reference import library as library_module

        if not download_module.offerable():
            return
        if library_module.discover():
            return                       # they already have one

        settings = QSettings()
        if settings.value(self.OFFER_SHOWN_SETTING, False, type=bool):
            return
        settings.setValue(self.OFFER_SHOWN_SETTING, True)

        installed = library_download.offer(self)
        if installed:
            self._fill_library_list(keep=installed)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt's name
        if self.worker and self.worker.isRunning():
            confirm = QMessageBox.question(
                self,
                "An analysis is still running",
                "Stop it and close TaxaTag?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.stop()
            self.worker.wait(10000)

        self.save_settings()
        event.accept()


def _percent_spin() -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 100.0)
    spin.setDecimals(1)
    spin.setSingleStep(0.5)
    spin.setSuffix(" % identical")
    return spin


def _padded(widget: QWidget, margin: int = 12) -> QWidget:
    """Wrap a widget so it does not sit flush against the tab border."""
    holder = QWidget()
    box = QVBoxLayout(holder)
    box.setContentsMargins(margin, margin, margin, margin)
    box.addWidget(widget)
    return holder

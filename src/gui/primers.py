# src/gui/primers.py
"""
Editing primer sets without touching a configuration file.

A primer set is the single most project-specific thing a user has to supply,
and the original pipeline had them written into the source code. Here they are
edited in a form, with the reverse complements worked out automatically so the
user only has to enter what is printed on their primer order.
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.gui.widgets import HelpLabel, SectionLabel
from src.reference import markers as markers_module
from src.utils.sequences import clean_primer, invalid_bases, reverse_complement

#: Ready-made primer sets, so a new user has something that works on day one.
#:
#: The first four are on by default: one per marker, the standard choice for
#: each. The rest are alternatives and ship switched off - see the comment
#: above them, and docs/protocols/adding-a-primer-preset.md for how each
#: earned its place. None was copied from a paper without being checked.
PRESETS: List[Dict] = [
    {
        "name": "MiFish_12S",
        "min_len": 163,
        "max_len": 185,
        "q_score": 20,
        "error_rate": 0.15,
        "abundance_filter": 0.0002,
        "primers": {
            "forward": "GTCGGTAAAACTCGTGCCAGC",
            "reverse": "CATAGTGGGGTATCTAATCCCAGTTTG",
        },
    },
    {
        "name": "MarVer3_16S",
        # Measured, not quoted. Across 81 Sussex samples the trimmed and
        # merged amplicon had a median of 220 bases, with sample medians
        # running 189-230; not one of the 81 fell inside the 232-274 window
        # usually cited for this primer pair, which describes the amplicon
        # with its primers still attached. Set wide enough to tolerate the
        # variation between samples.
        "min_len": 185,
        "max_len": 240,
        "q_score": 15,
        "error_rate": 0.20,
        "abundance_filter": 0.00025,
        "primers": {
            "forward": "AGACGAGAAGACCCTRTG",
            "reverse": "GGATTGCGCTGTTATCCC",
        },
    },
    {
        "name": "COI_Leray",
        "marker": "COI",
        # The Leray "mini-barcode": 313 bases from the 3' end of the classic
        # Folmer COI fragment, short enough to survive degraded environmental
        # DNA where the full 658-base barcode does not.
        "min_len": 290,
        "max_len": 330,
        "q_score": 20,
        "error_rate": 0.15,
        "abundance_filter": 0.0002,
        "primers": {
            # mlCOIintF
            "forward": "GGWACWGGWTGAACWGTWTAYCCYCC",
            # jgHCO2198. Published with inosine (I), which pairs with any base
            # and which TaxaTag reads as N.
            "reverse": "TANACYTCNGGRTGNCCRAARAAYCA",
        },
    },
    {
        "name": "18S_V4",
        "marker": "18S",
        # The V4 region of the eukaryote small-subunit rRNA gene. Its length
        # varies far more between taxa than a mitochondrial marker does, so
        # the window is correspondingly wide.
        "min_len": 350,
        "max_len": 500,
        "q_score": 20,
        "error_rate": 0.15,
        "abundance_filter": 0.0002,
        "primers": {
            # TAReuk454FWD1
            "forward": "CCAGCASCYGCGGTAATTCC",
            # TAReukREV3
            "reverse": "ACTTTCGTTCTTGATYRA",
        },
    },
    # ------------------------------------------------------------------
    # The sets below ship switched off. They are alternatives to the four
    # above rather than additions to them: one primer set per marker is what
    # an ordinary run needs, and a new user should not have to work out which
    # of two COI sets to remove. Tick one in the Primer sets tab to use it.
    #
    # Leaving them on was tried first and was wrong. COI_fwhF2 binds where
    # COI_Leray does, so a brand-new installation reported an overlap warning
    # on its first Check my setup - a real warning about a configuration
    # nobody had chosen.
    #
    # Each was checked against the installed reference library before being
    # included - see docs/protocols/adding-a-primer-preset.md for the numbers
    # and the method.
    #
    # Their length windows are estimated from the published fragment size
    # rather than measured on real runs, unlike MarVer3 above. Nothing is
    # filtered on them unless "Discard sequences outside the expected length"
    # is switched on, so an estimate that is slightly wide costs a stale note
    # in the report and nothing else.
    # ------------------------------------------------------------------
    {
        "name": "Tele02",
        "enabled": False,
        "marker": "12S",
        # Taberlet and Valentini's teleost pair, one of the two most-used 12S
        # sets in fish eDNA alongside MiFish. It binds the same region from a
        # few bases further in, so it does not compete with MiFish for a
        # sample - checked, not assumed.
        #
        # Measured on the 12S volume: 3.2% forward, 4.6% reverse, 2.9% both,
        # against 0.4% / 4.5% / 0.3% for MiFish on the same sample. MiFish is
        # the control and is known to work, so what these numbers say is that
        # the volume mostly holds whole mitochondrial genomes that do not span
        # either site - not that either primer is wrong.
        "min_len": 100,
        "max_len": 180,
        "q_score": 20,
        "error_rate": 0.15,
        "abundance_filter": 0.0002,
        "primers": {
            "forward": "AAACTCGTGCCAGCCACC",
            "reverse": "GGGTATCTAATCCCAGTTTG",
        },
    },
    {
        "name": "Elas02",
        "enabled": False,
        "marker": "12S",
        # Elasmobranchs - sharks, skates and rays - which the fish-tuned sets
        # amplify poorly. Worth having for any marine survey where they are
        # part of the question rather than a bycatch of it.
        #
        # 0.3% / 0.3% / 0.2%, level with the MiFish control on forward and
        # both. Its reverse scores far below MiFish's because the two differ
        # at two bases and this volume is overwhelmingly bony fish - which is
        # the reason the pair exists, showing up in the measurement.
        "min_len": 100,
        "max_len": 190,
        "q_score": 20,
        "error_rate": 0.15,
        "abundance_filter": 0.0002,
        "primers": {
            "forward": "GTTGGTHAATCTCGTGCCAGC",
            "reverse": "CATAGTAGGGTATCTAATCCTAGTTTG",
        },
    },
    # MiMammal was measured alongside these and is deliberately absent. Its
    # forward primer matched 0.0% of the sample - not "few", none - while its
    # reverse matched 4.5%, the same as MiFish's. A reverse that works and a
    # forward that matches nothing is the signature of a mis-transcribed
    # sequence, and protocol rule 4 says a low score with no reason that
    # predicts it is a transcription error until proven otherwise. It is not
    # shipped until somebody checks it against the paper.
    {
        "name": "COI_BF2BR2",
        "enabled": False,
        "marker": "COI",
        # Elbrecht & Leese (2017). Designed for freshwater invertebrates and
        # widely used in statutory biomonitoring. The most broadly matching
        # forward primer of any COI set tested here: 64% of a 5,000-reference
        # sample carried its binding site, against 21% for Leray.
        "min_len": 350,
        "max_len": 420,
        "q_score": 20,
        "error_rate": 0.15,
        "abundance_filter": 0.0002,
        "primers": {
            "forward": "GCHCCHGAYATRGCHTTYCC",
            "reverse": "TCDGGRTGNCCRAARAAYCA",
        },
    },
    {
        "name": "COI_fwhF2",
        "enabled": False,
        "marker": "COI",
        # Vamos, Elbrecht & Leese (2017). A ~205 base fragment, short enough
        # to survive badly degraded DNA where even the Leray mini-barcode
        # fails. Both of its binding sites are present in 14.5% of reference
        # sequences, the highest of any COI set tested.
        #
        # Its forward primer is mlCOIintF with two ambiguity codes widened, so
        # it binds everywhere COI_Leray does and then some. Switching both on
        # trims every COI sample twice and counts its reads under both names;
        # validate.check_primer_overlap warns about exactly this.
        "min_len": 150,
        "max_len": 220,
        "q_score": 20,
        "error_rate": 0.15,
        "abundance_filter": 0.0002,
        "primers": {
            "forward": "GGDACWGGWTGAACWGTWTAYCCHCC",
            "reverse": "GTRATWGCHCCDGCTARWACWGG",
        },
    },
    {
        "name": "Folmer_COI",
        "enabled": False,
        "marker": "COI",
        # Folmer et al. (1994): the original 658-base animal barcode, and
        # still what a lot of archived data was generated with. Included for
        # that data rather than recommended for new work - it carries no
        # ambiguity codes at all, which is why only 0.3% of reference
        # sequences match it exactly and why every set above exists.
        "min_len": 600,
        "max_len": 700,
        "q_score": 20,
        "error_rate": 0.15,
        "abundance_filter": 0.0002,
        "primers": {
            # LCO1490
            "forward": "GGTCAACAAATCATAAAGATATTGG",
            # HCO2198
            "reverse": "TAAACTTCAGGGTGACCAAAAAATCA",
        },
    },
    {
        "name": "18S_Uni",
        "enabled": False,
        "marker": "18S",
        # Zhan et al. (2013). An alternative V4 pair, common in ballast water
        # and invasive species surveys. It matched more of the reference
        # library at both ends than any other 18S set tested, including the
        # TAReuk pair above (45.6% against 42.9%).
        "min_len": 350,
        "max_len": 450,
        "q_score": 20,
        "error_rate": 0.15,
        "abundance_filter": 0.0002,
        "primers": {
            "forward": "AGGGCAAKYCTGGTGCCAGC",
            "reverse": "GRCGGTATCTRATCGYCTT",
        },
    },
    {
        "name": "18S_V9",
        "enabled": False,
        "marker": "18S",
        # Amaral-Zettler et al. (2009), as used by the Earth Microbiome
        # Project. A very short hypervariable region, so the window is wide
        # even by 18S standards. Its reverse primer sits at the extreme 3' end
        # of the gene, which most reference sequences are truncated before -
        # 61% carry the forward site but only 3% carry both.
        "min_len": 80,
        "max_len": 180,
        "q_score": 20,
        "error_rate": 0.15,
        "abundance_filter": 0.0002,
        "primers": {
            # Euk1391f
            "forward": "GTACACACCGCCCGTC",
            # EukBr
            "reverse": "TGATCCTTCTGCAGGTTCACCTAC",
        },
    },
]


def complete_locus(locus: Dict) -> Dict:
    """Fill in the reverse complements a locus needs before it can be used."""
    locus = copy.deepcopy(locus)
    # A set with no `enabled` key is one saved before the switch existed, and
    # was being looked for. Defaulting to True keeps an older settings file
    # behaving exactly as it did.
    locus["enabled"] = bool(locus.get("enabled", True))
    if not locus.get("marker"):
        # Recorded explicitly once worked out, so a later rename of the primer
        # set cannot silently change which reference data it is searched against.
        inferred = markers_module.infer_marker(locus.get("name", ""))
        if inferred:
            locus["marker"] = inferred
    primers = locus.setdefault("primers", {})
    primers["forward"] = clean_primer(primers.get("forward", ""))
    primers["reverse"] = clean_primer(primers.get("reverse", ""))
    primers["forward_rc"] = reverse_complement(primers["forward"])
    primers["reverse_rc"] = reverse_complement(primers["reverse"])
    return locus


def top_up_with_presets(loci: List[Dict]) -> List[Dict]:
    """
    Add any standard primer set the list does not already have, switched off.

    A settings file written before a preset existed does not know about it,
    and the only route to it is a menu item the user has to know to look for.
    That is how a settings file ends up with two primer sets covering two
    markers, on a program that supports four - and the symptom is not an
    error but a sample reported as having no recognisable primers.

    Each is added in the state it ships in, so an upgraded installation ends
    up looking like a new one: the standard set for each of the four markers
    ticked, the alternatives beside them unticked. One rule rather than two,
    and it is what "TaxaTag supports these four markers" has to mean.

    **Nothing already in the list is touched**, ticks included. Only sets that
    are absent are added.

    The cost of that is a set someone *deleted* comes back. The remedy is the
    one decision 0014 already recommends for a set you are not using: untick
    it rather than remove it. Nothing here can tell a set that was deleted
    from one that never existed, and coming back ticked is the mistake that
    shows itself - a marker appearing in the log - rather than the one that
    hides, which is a marker silently never looked for.

    Matched by name, case-insensitively, because that is what a user editing
    a settings file by hand would expect. A set the user renamed is left
    alone and its preset is added alongside; that is the safe way round.
    """
    existing = {str(locus.get("name", "")).strip().lower() for locus in loci}
    topped = [complete_locus(locus) for locus in loci]
    topped += [
        complete_locus(preset) for preset in PRESETS
        if preset["name"].strip().lower() not in existing
    ]
    return topped


class PrimerSetDialog(QDialog):
    """The form for one primer set."""

    def __init__(self, locus: Optional[Dict] = None, existing_names=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Primer set")
        self.setMinimumWidth(560)
        self._existing_names = {n.lower() for n in existing_names}
        self._original_name = (locus or {}).get("name", "")
        # Carried through the form rather than shown in it. Whether a set is
        # looked for is a per-run choice made in the list; editing its primers
        # is not the moment to change it, and silently switching a set back on
        # because someone corrected a typo would be worse than useless.
        self._enabled = bool((locus or {}).get("enabled", True))

        locus = locus or {}
        primers = locus.get("primers", {})

        self.name = QLineEdit(locus.get("name", ""))
        self.name.setPlaceholderText("for example, MiFish_12S")
        self.name.textChanged.connect(self._suggest_marker)

        # Which marker gene these primers amplify decides which part of a
        # reference library the sequences are searched against.
        self.marker = QComboBox()
        self.marker.addItem("Work it out from the name", "")
        for key in markers_module.known_markers():
            self.marker.addItem(f"{key} - {markers_module.MARKERS[key].label}", key)
        chosen = (locus.get("marker") or "").strip()
        index = self.marker.findData(chosen) if chosen else 0
        self.marker.setCurrentIndex(index if index >= 0 else 0)
        self.marker.currentIndexChanged.connect(self._suggest_marker)

        self.forward = QLineEdit(primers.get("forward", ""))
        self.forward.setPlaceholderText("GTCGGTAAAACTCGTGCCAGC")
        self.reverse = QLineEdit(primers.get("reverse", ""))
        self.reverse.setPlaceholderText("CATAGTGGGGTATCTAATCCCAGTTTG")

        self.forward_rc = QLabel("-")
        self.reverse_rc = QLabel("-")
        for label in (self.forward_rc, self.reverse_rc):
            label.setObjectName("computedValue")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)

        self.forward.textChanged.connect(self._update_complements)
        self.reverse.textChanged.connect(self._update_complements)

        self.min_len = QSpinBox()
        self.min_len.setRange(1, 100000)
        self.min_len.setValue(int(locus.get("min_len", 150)))
        self.max_len = QSpinBox()
        self.max_len.setRange(1, 100000)
        self.max_len.setValue(int(locus.get("max_len", 200)))

        self.q_score = QSpinBox()
        self.q_score.setRange(0, 60)
        self.q_score.setValue(int(locus.get("q_score", 20)))

        self.error_rate = QDoubleSpinBox()
        self.error_rate.setRange(0.0, 1.0)
        self.error_rate.setSingleStep(0.01)
        self.error_rate.setDecimals(2)
        self.error_rate.setValue(float(locus.get("error_rate", 0.15)))

        self.abundance = QDoubleSpinBox()
        self.abundance.setRange(0.0, 1.0)
        self.abundance.setDecimals(6)
        self.abundance.setSingleStep(0.0001)
        self.abundance.setValue(float(locus.get("abundance_filter", 0.0002)))

        form = QFormLayout()
        form.setSpacing(8)
        form.addRow("Name", self.name)
        form.addRow(HelpLabel("A short label for this primer set. It names the results folders."))
        form.addRow("Marker gene", self.marker)
        self.marker_hint = HelpLabel("")
        form.addRow(self.marker_hint)

        form.addRow(SectionLabel("Primers"))
        form.addRow("Forward primer", self.forward)
        form.addRow("Reverse primer", self.reverse)
        form.addRow(
            HelpLabel(
                "Enter the primers exactly as supplied. Ambiguity codes such as R, "
                "Y and N are fine. The reverse complements below are worked out for "
                "you and are what the pipeline uses to spot read-through."
            )
        )
        form.addRow("Forward, reverse complement", self.forward_rc)
        form.addRow("Reverse, reverse complement", self.reverse_rc)

        form.addRow(SectionLabel("Expected amplicon"))
        form.addRow("Shortest length (bp)", self.min_len)
        form.addRow("Longest length (bp)", self.max_len)
        form.addRow(
            HelpLabel(
                "The length of the region between the primers. Used to report "
                "whether your sequences came out the expected size."
            )
        )

        form.addRow(SectionLabel("Quality"))
        form.addRow("Minimum base quality", self.q_score)
        form.addRow(
            HelpLabel("Bases below this quality are trimmed from the ends of reads.")
        )
        form.addRow("Primer mismatch allowance", self.error_rate)
        form.addRow(
            HelpLabel(
                "How much of the primer may differ and still count as a match. "
                "0.15 allows roughly 15% of its length."
            )
        )
        form.addRow("Minimum share of a sample", self.abundance)
        form.addRow(
            HelpLabel(
                "A sequence must make up at least this fraction of a sample's reads "
                "to be reported. 0.0002 means one read in five thousand."
            )
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.addLayout(form)
        outer.addWidget(buttons)

        self._update_complements()
        self._suggest_marker()

    def _suggest_marker(self) -> None:
        """Show which marker the name implies, when none has been chosen."""
        if self.marker.currentData():
            self.marker_hint.setText(
                "Sequences from this primer set are searched against this marker's "
                "reference sequences."
            )
            return
        inferred = markers_module.infer_marker(self.name.text().strip())
        if inferred:
            self.marker_hint.setText(
                f"Recognised as {markers_module.describe(inferred)}"
            )
        else:
            self.marker_hint.setText(
                "The marker could not be worked out from this name. Choose it above, "
                "or sequences from this primer set cannot be searched against a "
                "reference library."
            )

    def _update_complements(self) -> None:
        forward = clean_primer(self.forward.text())
        reverse = clean_primer(self.reverse.text())
        self.forward_rc.setText(reverse_complement(forward) or "-")
        self.reverse_rc.setText(reverse_complement(reverse) or "-")

    def _accept_if_valid(self) -> None:
        problem = self._first_problem()
        if problem:
            QMessageBox.warning(self, "Check this primer set", problem)
            return
        self.accept()

    def _first_problem(self) -> Optional[str]:
        name = self.name.text().strip()
        if not name:
            return "Give this primer set a name."
        if name.lower() != self._original_name.lower() and name.lower() in self._existing_names:
            return f"There is already a primer set called '{name}'."

        for label, field in (("Forward", self.forward), ("Reverse", self.reverse)):
            sequence = clean_primer(field.text())
            if not sequence:
                return f"The {label.lower()} primer is empty."
            bad = invalid_bases(sequence)
            if bad:
                return (
                    f"The {label.lower()} primer contains characters that are not "
                    f"nucleotide codes: {bad}"
                )

        if self.min_len.value() > self.max_len.value():
            return "The shortest length cannot be greater than the longest length."

        # A primer set with no marker gene cannot be searched against anything.
        # It used to save anyway, with only a grey hint to say so, and the
        # consequence appeared an hour later: the sequences were trimmed,
        # merged and denoised, then dropped at the identification stage with
        # one warning line. Refused here instead, where the marker is in front
        # of the person who knows the answer.
        if not self.marker.currentData() and not markers_module.infer_marker(name):
            return (
                f"Choose the marker gene for '{name}'.\n\n"
                "The marker decides which part of a reference library these "
                "sequences are compared against, and it could not be worked "
                "out from the name. Without it, anything this primer set finds "
                "could not be identified."
            )
        return None

    def locus(self) -> Dict:
        """The primer set as configured, ready to be saved."""
        return complete_locus({
            "name": self.name.text().strip(),
            "marker": self.marker.currentData() or "",
            "enabled": self._enabled,
            "min_len": self.min_len.value(),
            "max_len": self.max_len.value(),
            "q_score": self.q_score.value(),
            "error_rate": round(self.error_rate.value(), 4),
            "abundance_filter": round(self.abundance.value(), 8),
            "primers": {
                "forward": clean_primer(self.forward.text()),
                "reverse": clean_primer(self.reverse.text()),
            },
        })


class PrimerSetPanel(QWidget):
    """The list of configured primer sets, with buttons to change it."""

    COLUMNS = ["Use", "Name", "Marker", "Forward primer", "Reverse primer",
               "Length (bp)", "Min. share"]

    #: Column holding the tick that decides whether a set is looked for.
    USE_COLUMN = 0

    #: Columns whose contents are nucleotide sequences, shown in a fixed-width
    #: font so bases line up and a typo is visible.
    SEQUENCE_COLUMNS = (3, 4)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loci: List[Dict] = []
        #: True while the table is being rebuilt from `_loci`, so that setting
        #: a tick programmatically is not mistaken for the user clicking it.
        self._loading = False
        #: Whether unticking a set asks first. False when the panel is being
        #: driven with nobody there to answer - the test suite, and anything
        #: that sets up a configuration without a person present. A modal
        #: dialog with no one to dismiss it does not warn anybody; it hangs.
        self.ask_before_switching_off = True
        #: Set when the warning's "don't ask again" box is ticked with a Yes.
        #: Deliberately not saved anywhere: it lasts until TaxaTag is next
        #: opened. A warning silenced for ever is one nobody at this machine
        #: sees again, and the second person to use it never had the choice.
        self._silenced_this_session = False

        intro = HelpLabel(
            "TaxaTag looks for each ticked primer set in every sample, and works "
            "out on its own which marker a sample was sequenced with. Add one set "
            "for each region you amplified, and untick any you are not using this "
            "time - an unticked set keeps its primers and can be switched back on."
        )

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self.edit_selected)
        self.table.itemChanged.connect(self._tick_changed)
        header = self.table.horizontalHeader()
        for column in range(len(self.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in self.SEQUENCE_COLUMNS:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)

        add = QPushButton("Add...")
        add.clicked.connect(self.add_new)
        edit = QPushButton("Edit...")
        edit.clicked.connect(self.edit_selected)
        duplicate = QPushButton("Duplicate")
        duplicate.clicked.connect(self.duplicate_selected)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_selected)
        presets = QPushButton("Add a standard set...")
        presets.clicked.connect(self.add_preset)

        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(edit)
        buttons.addWidget(duplicate)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        buttons.addWidget(presets)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(intro)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)

    # -- data ------------------------------------------------------------
    def set_loci(self, loci: List[Dict]) -> None:
        self._loci = [complete_locus(locus) for locus in (loci or [])]
        self._refresh()

    def loci(self) -> List[Dict]:
        return copy.deepcopy(self._loci)

    def _names(self) -> List[str]:
        return [locus.get("name", "") for locus in self._loci]

    def _refresh(self) -> None:
        self._loading = True
        try:
            self.table.setRowCount(len(self._loci))
            for row, locus in enumerate(self._loci):
                primers = locus.get("primers", {})
                enabled = bool(locus.get("enabled", True))

                tick = QTableWidgetItem("")
                tick.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                tick.setCheckState(
                    Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
                )
                tick.setToolTip("Look for this primer set when the pipeline runs")
                self.table.setItem(row, self.USE_COLUMN, tick)

                values = [
                    locus.get("name", ""),
                    locus.get("marker", "") or "?",
                    primers.get("forward", ""),
                    primers.get("reverse", ""),
                    f"{locus.get('min_len', '?')}-{locus.get('max_len', '?')}",
                    f"{locus.get('abundance_filter', 0):.5f}".rstrip("0").rstrip("."),
                ]
                for offset, value in enumerate(values):
                    column = offset + 1
                    item = QTableWidgetItem(str(value))
                    if column in self.SEQUENCE_COLUMNS:
                        item.setFont(_monospace(item.font()))
                    # Greyed rather than hidden, so a set that is off is still
                    # visibly there and its primers can be read.
                    if not enabled:
                        item.setForeground(_muted(item.foreground()))
                    self.table.setItem(row, column, item)
        finally:
            self._loading = False

    def _tick_changed(self, item: QTableWidgetItem) -> None:
        """Record a tick the user clicked, and grey the row to match."""
        if self._loading or item.column() != self.USE_COLUMN:
            return
        row = item.row()
        if not 0 <= row < len(self._loci):
            return

        wanted = item.checkState() == Qt.CheckState.Checked
        if not wanted and not self._confirm_switch_off(self._loci[row]):
            self._refresh()          # puts the tick back
            return
        self._loci[row]["enabled"] = wanted
        self._refresh()

    def _confirm_switch_off(self, locus: Dict) -> bool:
        """
        Ask before switching a primer set off.

        Switching one off is cheap to undo but not free: any samples of that
        marker stop being looked for, and an unfinished run can no longer be
        resumed past trimming, because the trimmed folders were made with a
        different set of primers in play. Both are worth knowing before the
        click rather than after it.

        Turning one *on* is not confirmed. It can only add work, never discard
        any.
        """
        if not self.ask_before_switching_off or self._silenced_this_session:
            return True
        name = locus.get("name", "this primer set")
        marker = locus.get("marker") or markers_module.infer_marker(name) or "its marker"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Stop looking for this primer set?")
        box.setText(
            f"'{name}' will not be looked for in any sample.{chr(10)}{chr(10)}"
            f"Anything sequenced with {marker} primers will be reported as "
            f"having no recognised primers, and will not reach the results.{chr(10)}{chr(10)}"
            "If you have a run waiting to be resumed, it will have to start "
            "again from trimming: the trimmed reads were produced with a "
            f"different set of primers.{chr(10)}{chr(10)}"
            "Its primers are kept either way, so you can switch it back on."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        again = QCheckBox("Don't ask again until TaxaTag is next opened")
        box.setCheckBox(again)
        answer = box.exec()
        # Honoured only with a Yes. Somebody who ticks the box and then
        # cancels has just been talked out of it; silencing the warning on
        # that answer would let the next click through unwarned.
        if answer == QMessageBox.StandardButton.Yes and again.isChecked():
            self._silenced_this_session = True
        return answer == QMessageBox.StandardButton.Yes

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    # -- actions ---------------------------------------------------------
    def add_new(self) -> None:
        dialog = PrimerSetDialog(existing_names=self._names(), parent=self)
        if dialog.exec():
            self._loci.append(dialog.locus())
            self._refresh()

    def edit_selected(self) -> None:
        row = self._selected_row()
        if row < 0:
            QMessageBox.information(self, "Nothing selected", "Choose a primer set to edit.")
            return
        others = [n for i, n in enumerate(self._names()) if i != row]
        dialog = PrimerSetDialog(self._loci[row], existing_names=others, parent=self)
        if dialog.exec():
            self._loci[row] = dialog.locus()
            self._refresh()

    def duplicate_selected(self) -> None:
        row = self._selected_row()
        if row < 0:
            QMessageBox.information(self, "Nothing selected", "Choose a primer set to copy.")
            return
        copy_of = copy.deepcopy(self._loci[row])
        copy_of["name"] = _unique_name(copy_of.get("name", "Copy"), self._names())
        self._loci.append(copy_of)
        self._refresh()

    def remove_selected(self) -> None:
        row = self._selected_row()
        if row < 0:
            QMessageBox.information(self, "Nothing selected", "Choose a primer set to remove.")
            return
        name = self._loci[row].get("name", "this primer set")
        confirm = QMessageBox.question(
            self,
            "Remove primer set",
            f"Remove '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            del self._loci[row]
            self._refresh()

    def add_preset(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        available = [p["name"] for p in PRESETS if p["name"] not in self._names()]
        if not available:
            QMessageBox.information(
                self, "Already added", "Every standard primer set is already in the list."
            )
            return
        name, chosen = QInputDialog.getItem(
            self, "Add a standard primer set", "Primer set:", available, 0, False
        )
        if chosen and name:
            preset = next(p for p in PRESETS if p["name"] == name)
            self._loci.append(complete_locus(preset))
            self._refresh()


def _unique_name(base: str, existing: List[str]) -> str:
    """Append a number until the name is not already taken."""
    taken = {n.lower() for n in existing}
    candidate = f"{base}_copy"
    index = 2
    while candidate.lower() in taken:
        candidate = f"{base}_copy{index}"
        index += 1
    return candidate


def _monospace(font):
    from PyQt6.QtGui import QFont

    font.setFamily("Consolas")
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def _muted(brush):
    """
    The row colour for a primer set that is switched off.

    Derived from whatever colour the row would have had rather than set to a
    fixed grey, so it stays readable if the application is ever themed dark.
    """
    from PyQt6.QtGui import QBrush, QColor, QPalette

    colour = brush.color() if brush.style() != Qt.BrushStyle.NoBrush else None
    if colour is None or not colour.isValid():
        colour = QPalette().color(QPalette.ColorRole.WindowText)
    faded = QColor(colour)
    faded.setAlpha(110)
    return QBrush(faded)

"""
Offering to fetch a reference library, and showing it happening.

TaxaTag without a library still works - it can search NCBI over the web -
but it is a different program: minutes per sample instead of seconds, and
useless without a connection. So the library is the first thing a new user
needs and the last thing they would think to go looking for, since nothing
in the window says a two-gigabyte download exists.

It is therefore offered rather than waited for. The offer appears once, on
a first run with no library, and after that it lives in the library list as
a permanently available entry: greyed, so it reads as secondary to the
libraries actually on the machine, but selectable, so it is a way back
rather than a dead label. That distinction is the whole design of the entry
and it is easy to get wrong, because Qt's own idea of "greyed" - clearing
`ItemIsEnabled` - makes an item that cannot be clicked at all, which would
turn the reminder into a taunt.

Nothing is downloaded without a click. A program that begins a two-gigabyte
transfer because it was opened is a program installed on a metered
connection once.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

#: Marks the library-list entry that offers a download rather than naming a
#: library. Distinct from the browse entry so neither can be mistaken for a
#: folder somebody chose.
DOWNLOAD_A_LIBRARY = "__download__"


def label_for(entry) -> str:
    """How the offer reads in the library list."""
    return f"Download the {entry.name} library  -  {entry.describe_size()}"


def grey_out(combo, index: int) -> None:
    """
    Make an item read as secondary while leaving it selectable.

    Deliberately not `setEnabled(False)`, which is Qt's greying and also
    removes the item from selection entirely - the user would see the
    reminder and be unable to act on it. Colouring the foreground role
    gives the appearance without the refusal.
    """
    palette = combo.palette()
    muted = palette.color(palette.ColorGroup.Disabled, palette.ColorRole.Text)
    combo.setItemData(index, QColor(muted), Qt.ItemDataRole.ForegroundRole)


class LibraryDownloadDialog(QDialog):
    """
    The offer, and then the download itself, in one window.

    One window rather than two because they are one decision: a user who
    says yes should not have to find a second dialog, and one who stops
    half way should land back on the offer rather than on nothing.
    """

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.worker = None
        self.installed = ""

        self.setWindowTitle(f"Download the {entry.name} library")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        headline = QLabel(f"<b>{entry.name}</b>")
        layout.addWidget(headline)

        described = QLabel(entry.description)
        described.setWordWrap(True)
        layout.addWidget(described)

        # The size, before the button rather than after it. Somebody on a
        # phone tether needs this to be the thing they read, not something
        # they discover once it is running.
        self.detail = QLabel(
            f"{entry.describe_size()}.<br>"
            "TaxaTag works without it by searching NCBI over the web, which "
            "is slower and needs a connection. A library is searched on this "
            "computer, in seconds, offline."
        )
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        if entry.doi:
            citation = QLabel(
                f"Published at <a href='https://doi.org/{entry.doi}'>"
                f"doi.org/{entry.doi}</a>, which is what to cite."
            )
            citation.setOpenExternalLinks(True)
            citation.setWordWrap(True)
            layout.addWidget(citation)

        self.bar = QProgressBar()
        self.bar.setVisible(False)
        layout.addWidget(self.bar)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.start_button = QPushButton("Download")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.start)
        self.close_button = QPushButton("Not now")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.start_button)
        layout.addLayout(buttons)

    def start(self) -> None:
        from src.gui.worker import LibraryDownloadWorker
        from src.reference import download

        fits, why = download.enough_space(self.entry)
        if not fits:
            QMessageBox.warning(self, "Not enough room", why)
            return

        self.start_button.setEnabled(False)
        self.close_button.setText("Stop")
        self.bar.setVisible(True)
        self.status.setVisible(True)
        self.status.setText("Starting...")

        self.worker = LibraryDownloadWorker(self.entry, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.completed.connect(self._on_completed)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, done: int, total: int, what: str) -> None:
        if total > 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(100 * done / total))
            self.status.setText(
                f"{what}  -  {done / 1e9:.2f} of {total / 1e9:.2f} GB"
            )
        else:
            # A server that declines to say how large the file is, or a step
            # whose length is not known. A bar that cannot say how far along
            # it is should say that rather than invent a number.
            self.bar.setRange(0, 0)
            self.status.setText(what)

    def _on_completed(self, folder: str) -> None:
        self.installed = folder
        if not folder:
            # Stopped rather than finished. The partly-downloaded file is
            # kept, so say so - otherwise the next attempt looks like
            # starting over and nobody tries.
            self.bar.setVisible(False)
            self.status.setText(
                "Stopped. What was downloaded is kept, so starting again "
                "will carry on from here rather than begin afresh."
            )
            self.start_button.setEnabled(True)
            self.close_button.setText("Close")
            return
        self.accept()

    def _on_failed(self, why: str) -> None:
        self.bar.setVisible(False)
        self.status.setText(why)
        self.start_button.setEnabled(True)
        self.start_button.setText("Try again")
        self.close_button.setText("Close")

    def reject(self) -> None:
        """"Not now" before it starts; "Stop" once it has."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self.status.setText("Stopping...")
            return
        super().reject()


def offer(parent, entry=None) -> Optional[str]:
    """
    Show the offer, and return the installed folder if one was downloaded.

    Returns None when there is nothing to offer, which is the state until a
    library has actually been published - an entry with no checksum is not
    offerable, on purpose.
    """
    from src.reference import download

    if entry is None:
        available = download.offerable()
        if not available:
            return None
        entry = available[0]

    dialog = LibraryDownloadDialog(entry, parent)
    dialog.exec()
    return dialog.installed or None

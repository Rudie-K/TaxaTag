# src/gui/update_offer.py
"""
Telling somebody an update exists, and getting out of the way if they say no.

The check runs on a background thread at startup and says nothing at all
unless there is something to say. No "checking for updates", no "you are up
to date", no error when the network is absent - a user who is offline is not
having a problem this window could help with.

Applying it is where the two kinds part company. A **library** is replaced by
`reference/download.py` while TaxaTag keeps running, because nothing holds it
open. The **application** cannot be: Windows will not let a running
executable be overwritten, so the update has to outlive the process that
started it. The installer is handed off and this process exits - which works
because the installer is an Inno Setup upgrade with a fixed `AppId`, so it
finds the existing installation, replaces it, and offers to start it again.
Nothing here writes to Program Files itself.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from src.utils import updates
from src.utils.updates import Update


class UpdateCheckWorker(QThread):
    """
    Asks GitHub what exists, off the main thread.

    A network call on the GUI thread freezes the window, and this one runs
    while the user is trying to open the program - the worst possible moment
    for it. `updates.pending` never raises, so there is no failure signal:
    nothing found and nothing reachable look the same on purpose.
    """

    found = pyqtSignal(object)                    # List[Update]

    def run(self) -> None:                        # called by Qt on the new thread
        try:
            self.found.emit(updates.pending())
        except Exception:                         # pragma: no cover - belt and braces
            # An update check must never be the reason a program fails to
            # start. There is nothing here worth reporting to a user.
            self.found.emit([])


class UpdateDialog(QDialog):
    """
    The three answers, spelled out rather than implied by an OK and a Cancel.

    A user dismissing this should know what dismissing it means, which an
    `OK`/`Cancel` pair cannot tell them. "Not now" comes back; "skip this
    version" does not, until there is a different one.
    """

    def __init__(self, update: Update, parent=None):
        super().__init__(parent)
        self.update_offered = update
        self.answer: Optional[str] = None
        self.setWindowTitle(f"An update for {update.name}")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)

        headline = QLabel(f"<b>{update.name} {update.version} is available.</b>")
        headline.setWordWrap(True)
        layout.addWidget(headline)

        size = update.describe_size()
        if update.channel == "application":
            detail = (
                "TaxaTag will close and the installer will take over. Your "
                "settings, results and reference libraries are not touched."
            )
        else:
            detail = (
                "The library will be downloaded and checked before anything "
                "already installed is replaced. You can keep working; the "
                "current library stays in place until the new one is ready."
            )
        if size:
            detail = f"{size} to download. {detail}"
        explanation = QLabel(detail)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        if update.landing_page:
            link = QLabel(
                f'<a href="{update.landing_page}">What changed in this version</a>'
            )
            link.setOpenExternalLinks(True)
            link.setWordWrap(True)
            layout.addWidget(link)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        buttons = QDialogButtonBox()
        self.update_button = QPushButton("Update")
        self.update_button.setDefault(True)
        self.later_button = QPushButton("Remind me later")
        self.never_button = QPushButton("Don't ask again")
        # Roles rather than positions: on Windows the accept role is placed
        # first, and a user who has learned where "Update" sits should find
        # it in the same place in every dialog the system shows them.
        buttons.addButton(self.update_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.later_button, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.addButton(self.never_button, QDialogButtonBox.ButtonRole.DestructiveRole)
        layout.addWidget(buttons)

        self.update_button.clicked.connect(self._accepted)
        self.later_button.clicked.connect(self._later)
        self.never_button.clicked.connect(self._never)

    # Closing the window with the X is "later", not "never". A dialog that
    # silenced itself because somebody hit Escape would be a trap.
    def reject(self) -> None:
        if self.answer is None:
            self._later()
        else:
            super().reject()

    def _later(self) -> None:
        self.answer = updates.LATER
        updates.remember(
            self.update_offered.channel, updates.LATER, self.update_offered.version
        )
        super().reject()

    def _never(self) -> None:
        self.answer = updates.NEVER
        updates.remember(
            self.update_offered.channel, updates.NEVER, self.update_offered.version
        )
        super().reject()

    def _accepted(self) -> None:
        self.answer = "update"
        self.accept()


def _download_verified(update: Update, into: Path, progress=None) -> Optional[Path]:
    """
    Fetch an installer and prove it is the published one before returning it.

    Returns None if it could not be fetched or does not match. There is no
    third outcome and no "continue anyway": this file is about to be executed
    with the user's privileges, so an unverified one is not a degraded
    download, it is something that must not be run.
    """
    import requests

    target = into / (update.url.rsplit("/", 1)[-1] or "TaxaTag-Setup.exe")
    digest = hashlib.sha256()
    done = 0
    try:
        with requests.get(update.url, stream=True, timeout=60) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or update.size_bytes or 0)
            with open(target, "wb") as handle:
                for block in response.iter_content(chunk_size=1 << 20):
                    handle.write(block)
                    digest.update(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
    except Exception:
        target.unlink(missing_ok=True)
        return None

    if digest.hexdigest().lower() != update.sha256.strip().lower():
        target.unlink(missing_ok=True)
        return None
    return target


def _hand_over_to_installer(installer: Path) -> bool:
    """
    Start the installer as a process of its own and report whether it began.

    The detaching is in `utils/process.hand_off`, which owns launching - this
    only decides that an installer is a thing worth handing off.

    The installer needs to write to Program Files, so Windows will raise a
    consent prompt. That is not something to work around; it is the operating
    system asking the user to confirm an action taken on their behalf, which
    is exactly what this is.
    """
    if sys.platform != "win32":
        return False
    from src.utils import process

    return process.hand_off([installer], working_directory=installer.parent)


def apply_application_update(update: Update, parent=None) -> bool:
    """
    Download, verify, hand over, and ask the window to close.

    Returns True when the installer has been started, in which case the
    caller should quit - anything else it does afterwards is happening in a
    program that is about to be replaced.
    """
    folder = Path(tempfile.gettempdir()) / "TaxaTag-update"
    folder.mkdir(parents=True, exist_ok=True)

    installer = _download_verified(update, folder)
    if installer is None:
        QMessageBox.warning(
            parent,
            "The update could not be verified",
            "The installer either did not download or did not match the "
            "checksum published with it, so it has not been run. Nothing on "
            "this computer has changed.\n\nTaxaTag will carry on as it is; "
            "you can try again later, or download the update yourself from "
            "the releases page.",
        )
        return False

    if not _hand_over_to_installer(installer):
        QMessageBox.information(
            parent,
            "Finish the update by hand",
            "The installer was downloaded and checked, but could not be "
            f"started automatically. It is here:\n\n{installer}\n\n"
            "Close TaxaTag and run it when you are ready.",
        )
        return False
    return True


def apply_library_update(update: Update, parent=None) -> bool:
    """
    Re-install a library that has been republished.

    Handed to the dialog that already does this, rather than repeated here.
    `download.install` unpacks to a staging folder and only replaces what is
    in place once the new copy has been verified, so a failed update leaves
    the working library working.
    """
    from src.gui import library_download
    from src.reference import download

    entry = download.AVAILABLE.get(update.channel)
    if entry is None:
        return False
    return bool(library_download.offer(parent, entry))


def act_on(update: Update, parent=None) -> bool:
    """
    Show the offer and carry out whatever was chosen.

    Returns True only when the application is being replaced and the caller
    must quit. Everything else - a library updated, a refusal, a failure -
    leaves TaxaTag running and returns False.
    """
    dialog = UpdateDialog(update, parent)
    dialog.exec()
    if dialog.answer != "update":
        return False

    if update.channel == "application":
        return apply_application_update(update, parent)
    apply_library_update(update, parent)
    return False


def check_in_background(window) -> Optional[UpdateCheckWorker]:
    """
    Start the check and arrange for the answer to be acted on.

    Returns the worker so the caller can hold it: a QThread whose last Python
    reference is dropped is destroyed while still running, which Qt reports
    by terminating the program.

    Does nothing at all when TaxaTag is not an installed application - a
    developer running from source is not who this is for, and offering to
    replace a source checkout with an installer would be wrong.
    """
    if not getattr(sys, "frozen", False) and not os.environ.get("TAXATAG_CHECK_UPDATES"):
        return None

    worker = UpdateCheckWorker(window)

    def _arrived(found: List[Update]) -> None:
        for update in found:
            if act_on(update, window):
                QApplication.instance().quit()
                return

    worker.found.connect(_arrived, Qt.ConnectionType.QueuedConnection)
    worker.start()
    return worker

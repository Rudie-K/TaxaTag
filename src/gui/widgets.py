# src/gui/widgets.py
"""
Small reusable pieces of the interface.

Kept apart from the main window so that window stays a description of the
layout rather than a wall of widget plumbing.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from src.utils import paths
from typing import Optional

from PyQt6.QtCore import QEvent, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QWidget,
)

from src.gui import theme
from src.utils.reporting import Reporter

#: The kinds of log line. Their colours live in the palette, one set per
#: scheme, under `log_<level>` - here they were a second dictionary and the
#: one most likely to be left light when the rest went dark.
LOG_LEVELS = ("heading", "info", "debug", "success", "warning", "error")


def log_colour(level: str) -> str:
    """The colour for a log line of this level, in the scheme on screen."""
    palette = theme.active()
    return palette.get(f"log_{level}", palette["log_info"])


class PathPicker(QWidget):
    """
    A text box with a Browse button, for choosing a file or folder.

    It also accepts a folder dragged onto it, which for somebody who does
    not use a command line is the obvious gesture and was the one thing the
    window did not answer. Dropping is offered here rather than on the
    window as a whole, so that the target of the drop is unambiguous: a
    window-wide drop would have to guess whether a folder was meant as the
    reads or as the results, and guessing wrong sends an analysis somewhere
    the user did not choose.
    """

    #: Every edit, including half a word. Cheap reactions only.
    changed = pyqtSignal(str)

    #: A value worth acting on, for anything that reads the disk.
    #:
    #: The difference is where the value came from. Typing produces a burst
    #: of paths that name nothing - "C", "C:", "C:\\U" - so the useful moment
    #: is when it stops. Browsing, dropping a folder, or loading a settings
    #: file each produce one complete path in one go, and waiting on those
    #: would only add a delay to a decision already made.
    #:
    #: This exists because both consumers of `changed` read the disk: one
    #: globs the reads folder, the other reads a manifest for every past run
    #: and loads a table. On a results folder holding a hundred runs that was
    #: a quarter of a second per character typed.
    settled = pyqtSignal(str)

    #: How long typing has to stop before the value counts as settled.
    SETTLE_MS = 400

    def __init__(
        self,
        placeholder: str = "",
        mode: str = "folder",
        file_filter: str = "All files (*)",
        parent=None,
    ):
        super().__init__(parent)
        self.mode = mode
        self.file_filter = file_filter

        self.field = QLineEdit()
        self.field.setPlaceholderText(placeholder)
        # A screen reader lands on the text box, not on this widget, and the
        # form's label is this widget's buddy, so without a name of its own
        # the box announces as "edit". `set_label` replaces this with the
        # row's label once the form has one.
        self.field.setAccessibleName(placeholder)
        self.field.textChanged.connect(self.changed.emit)

        self._settling = QTimer(self)
        self._settling.setSingleShot(True)
        self._settling.setInterval(self.SETTLE_MS)
        self._settling.timeout.connect(lambda: self.settled.emit(self.value()))
        self.field.textChanged.connect(lambda _: self._settling.start())

        browse = QPushButton("Browse...")
        self.browse_button = browse
        # Its own width, not a fixed one: a width pinned at the normal text
        # size clipped the button to "rowse" at the large one.
        browse.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        browse.clicked.connect(self._browse)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.field)
        row.addWidget(browse)

        # On the whole widget and on the text box, because Qt delivers the
        # drop to whichever is under the pointer and the text box covers
        # most of the row. Without the second, a drop aimed at the obvious
        # target would be refused.
        self.setAcceptDrops(True)
        self.field.setAcceptDrops(True)
        self.field.installEventFilter(self)

    # ------------------------------------------------------------------
    # Accepting a dropped folder
    # ------------------------------------------------------------------
    def acceptable(self, url) -> Optional[str]:
        """
        The local path in `url` if this picker would take it, else None.

        A picker asking for a folder accepts a folder; one asking for a file
        accepts either, taking the containing folder if a file is dropped on
        a folder picker. That last case is the common accident - somebody
        drags one of their reads rather than the folder holding them - and
        doing the obvious thing beats refusing on a technicality.
        """
        if not url.isLocalFile():
            return None
        try:
            path = Path(url.toLocalFile())
            if self.mode == "folder":
                if path.is_dir():
                    return str(path)
                # A file dropped where a folder is wanted: take its folder.
                return str(path.parent) if path.is_file() else None
            return str(path) if path.exists() else None
        except OSError:
            # A path the operating system will not describe is simply not
            # accepted. `is_dir` re-raises anything pathlib does not consider
            # ordinary, and a refused drop must not take out the window.
            return None

    def eventFilter(self, watched, event):     # noqa: N802 - Qt's name
        """Give the text box the same behaviour as the row around it."""
        if watched is self.field:
            if event.type() == QEvent.Type.DragEnter:
                self.dragEnterEvent(event)
                return event.isAccepted()
            if event.type() == QEvent.Type.Drop:
                self.dropEvent(event)
                return event.isAccepted()
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event) -> None:   # noqa: N802 - Qt's name
        data = event.mimeData()
        if data.hasUrls() and any(self.acceptable(url) for url in data.urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:        # noqa: N802 - Qt's name
        for url in event.mimeData().urls():
            chosen = self.acceptable(url)
            if chosen:
                # The first acceptable one only. Several folders dropped
                # together is not a request to analyse several folders -
                # there is one box - and silently taking the last would make
                # the outcome depend on the order the file manager happened
                # to hand them over.
                self.field.setText(chosen)
                self._settle_now()
                event.acceptProposedAction()
                return
        event.ignore()

    def _browse(self) -> None:
        current = self.field.text().strip()
        # A typed path the operating system refuses must not crash Browse.
        start = current if current and paths.exists(current) else str(Path.home())

        if self.mode == "folder":
            chosen = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        elif self.mode == "save":
            chosen, _ = QFileDialog.getSaveFileName(self, "Choose a file", start, self.file_filter)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, "Choose a file", start, self.file_filter)

        if chosen:
            self.field.setText(chosen)
            self._settle_now()

    def _settle_now(self) -> None:
        """Announce a complete path immediately, cancelling any wait."""
        self._settling.stop()
        self.settled.emit(self.value())

    def value(self) -> str:
        return self.field.text().strip()

    def set_value(self, value) -> None:
        self.field.setText(str(value) if value else "")
        self._settle_now()

    def path(self) -> Optional[Path]:
        text = self.value()
        return Path(text) if text else None

    def set_label(self, label: str) -> None:
        """What a screen reader calls this picker: the form row's label."""
        self.field.setAccessibleName(label)
        self.browse_button.setAccessibleName(f"Browse for {label[:1].lower()}{label[1:]}")


class LogView(QTextEdit):
    """
    The scrolling record of what the pipeline is doing.

    Colour-coded by severity so a warning is noticeable without the user
    having to read every line, and capped in length so an overnight run does
    not slowly eat all the memory on the machine.
    """

    MAX_BLOCKS = 5000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.document().setMaximumBlockCount(self.MAX_BLOCKS)
        self.setAccessibleName("Progress log")
        # What has been written, with its level. A line's colour is baked
        # into the document as it is appended, so a change of scheme cannot
        # reach it through the stylesheet; this is what `repaint_lines`
        # rebuilds from. Capped the same way the document is.
        self._lines: deque = deque(maxlen=self.MAX_BLOCKS)
        # The face and size come from the stylesheet, under this name, so
        # that the text-size setting and the dyslexia-friendly font reach
        # the log the same way they reach everything else.
        self.setObjectName("logView")

    def append_line(self, text: str, level: str = "info") -> None:
        self._lines.append((text, level))
        self._write(text, level)

        # Only follow the tail when the user is already at the bottom, so
        # scrolling back to read something is not yanked away from them.
        scrollbar = self.verticalScrollBar()
        if scrollbar.value() >= scrollbar.maximum() - 40:
            scrollbar.setValue(scrollbar.maximum())

    def _write(self, text: str, level: str) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(log_colour(level)))
        if level in ("heading", "error"):
            fmt.setFontWeight(QFont.Weight.Bold)

        if level == "heading":
            cursor.insertText("\n", QTextCharFormat())
        # The same [OK] / [!] / [ERROR] the terminal prints, so that severity
        # is carried by the words and not only by the colour.
        cursor.insertText(Reporter.marked(text.rstrip(), level) + "\n", fmt)

    def repaint_lines(self) -> None:
        """Redraw every line in the scheme now on screen."""
        scrollbar = self.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 40
        position = scrollbar.value()
        # Qt's clear, not ours: the record is what is being replayed.
        super().clear()
        for text, level in self._lines:
            self._write(text, level)
        scrollbar.setValue(scrollbar.maximum() if at_bottom else position)

    def clear(self) -> None:  # noqa: D102 - QTextEdit's name
        # The record goes with the text, or a repaint resurrects a run the
        # user has already cleared away.
        self._lines.clear()
        super().clear()

    def save_to(self, path: Path) -> None:
        path.write_text(self.toPlainText(), encoding="utf-8")


class CheckListView(QTextEdit):
    """Shows the result of the pre-flight checks in plain language."""

    SYMBOLS = {"ok": "OK", "warning": "NOTE", "error": "PROBLEM"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setObjectName("checkList")
        self.setAccessibleName("Setup check results")
        self._report = None

    def repaint_lines(self) -> None:
        """Redraw the last report in the scheme now on screen."""
        if self._report is not None:
            self.show_report(self._report)

    def show_report(self, report) -> None:
        self._report = report
        muted = theme.active()["muted"]
        rows = []
        for check in report.checks:
            colour = log_colour(
                {"ok": "success", "warning": "warning", "error": "error"}[check.status]
            )
            symbol = self.SYMBOLS[check.status]
            fix = (
                f'<div style="margin:2px 0 0 22px;color:{muted};">{_escape(check.fix)}</div>'
                if check.fix
                else ""
            )
            rows.append(
                f'<div style="margin-bottom:8px;">'
                f'<span style="color:{colour};font-weight:600;">[{symbol}]</span> '
                f"<b>{_escape(check.name)}</b> &mdash; {_escape(check.message)}"
                f"{fix}</div>"
            )
        self.setHtml("".join(rows))


class SectionLabel(QLabel):
    """A heading that separates one group of settings from the next."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("sectionLabel")


class HelpLabel(QLabel):
    """
    A short explanation under a setting, in the user's own language.

    The size policy carries `setHeightForWidth(True)`, which is not
    decoration. `QLabel` computes `heightForWidth` correctly for wrapped
    text, but `QWidget.hasHeightForWidth` reads the *policy* flag, and
    `setWordWrap(True)` does not set it. So a wrapped label answers "no, my
    height does not depend on my width" while being a widget whose height
    depends on nothing else, and every layout above it believes the denial
    and falls back to `sizeHint`.

    That hint is computed at a width Qt guesses, not the width the label
    gets. Measured here: this label was handed 1044 pixels, where its text
    needs 26, and reported a hint of 68 - the height of four lines at the
    468-pixel width Qt guessed. Three such labels in one form left 98 pixels
    of blank space in a 297-pixel box, which read as a padding bug and was
    in fact a one-flag lie about geometry.
    """

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("helpLabel")
        self.setWordWrap(True)
        policy = QSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)


def horizontal_rule() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setObjectName("rule")
    return line


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


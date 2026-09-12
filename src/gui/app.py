# src/gui/app.py
"""Starts the graphical interface."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from src.gui import theme
from src.gui.main_window import APP_NAME, MainWindow
from src.utils import platform as platform_utils
from src.utils import paths


def _icon() -> Optional[QIcon]:
    for name in ("taxatag.ico", "taxatag.png"):
        candidate = platform_utils.app_root() / "resources" / name
        if candidate.exists():
            return QIcon(str(candidate))
    return None


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv)

    # High-DPI scaling is on by default in Qt 6, but the rounding policy still
    # has to be asked for or text is blurry at 125% and 150% zoom, which is
    # what most Windows laptops ship with.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("TaxaTag")
    app.setStyle("Fusion")
    # Before the window exists, so nothing is ever drawn in the wrong scheme
    # and then redrawn. Organisation and application names must already be
    # set - QSettings keys on them.
    theme.apply(app, theme.resolve(theme.saved_choice()))

    icon = _icon()
    if icon:
        app.setWindowIcon(icon)

    # A settings file may be passed on the command line, which is how a lab
    # can hand out a preconfigured shortcut.
    config_path = None
    for argument in argv[1:]:
        candidate = Path(argument)
        if candidate.suffix.lower() in (".yaml", ".yml") and paths.exists(candidate):
            config_path = candidate
            break

    window = MainWindow(config_path)
    window.show()
    # After show(), so the offer appears over a drawn window rather than in
    # front of a grey rectangle - and only when there is no library and one
    # is actually published to offer.
    window.offer_a_library_if_there_is_none()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

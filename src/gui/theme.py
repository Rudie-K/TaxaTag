# src/gui/theme.py
"""
The application's visual style, in light and in dark.

Qt's default look differs noticeably between Windows, macOS and Linux. A
single stylesheet keeps the tool recognisable in a screenshot taken on any of
them, which matters for a program whose documentation has to serve all three.

Every colour lives in one palette per scheme, including the colours of the
log lines. They used to be a second dictionary in `widgets.py`, which was the
one most likely to be forgotten when a scheme was added: a dark background
needs its own greens and ambers, and nothing would have said so. A test now
insists that every palette carries the same keys.

Two things are set, not one. The stylesheet paints the widgets; the
`QPalette` is what code that asks Qt for a colour gets back - the primer
table's greyed rows, the library list's secondary entries - and a stylesheet
alone leaves it at Fusion's light-mode black. Setting only the stylesheet
makes a dark window with black text in the places that were written to adapt.
"""

from __future__ import annotations

from typing import Dict, Optional

#: The three things a user can ask for. `SYSTEM` follows the operating
#: system's own preference, and is the default so that a first run looks
#: like everything else on the desktop.
SYSTEM, LIGHT, DARK = "system", "light", "dark"
CHOICES = (SYSTEM, LIGHT, DARK)

# Colours are deliberately low-contrast except where something needs
# attention, so that a warning in the log stands out on its own. Every
# text-on-surface pair in both schemes meets WCAG AA (4.5:1) and
# `tools/contrast.py` measures that - eyeballing a dark scheme is how a grey
# ends up unreadable on a laptop in daylight.
PALETTES: Dict[str, Dict[str, str]] = {
    LIGHT: {
        "background": "#f6f7f9",
        "panel": "#ffffff",
        "panel_sunken": "#f0f1f3",      # disabled fields, the plan box
        "header": "#eef1f4",            # table column headings
        "gridline": "#eceef0",
        "border": "#d8dce1",
        "border_faint": "#e6e8eb",
        "text": "#24292f",
        "muted": "#57606a",
        "text_disabled": "#9aa1a9",
        "pressed": "#eef2f6",
        "accent": "#1a4d7a",
        "accent_hover": "#215f96",
        "accent_pressed": "#143c60",
        "accent_disabled": "#9fb3c4",
        "on_accent": "#ffffff",         # text on a filled button
        "danger": "#b21f2d",
        "danger_hover": "#c8303e",
        "log_heading": "#1a4d7a",
        "log_info": "#24292f",
        "log_debug": "#6e7781",
        "log_success": "#116329",
        "log_warning": "#9a5b00",
        "log_error": "#b21f2d",
    },
    # A sibling of the light scheme rather than an inversion of it: the same
    # low-contrast surfaces, the same quiet greys, the accent lifted just
    # enough to read. Filled buttons carry dark text here, because a pale
    # accent under white lettering is the commonest dark-mode mistake.
    DARK: {
        "background": "#15191f",
        "panel": "#1e242c",
        "panel_sunken": "#262d36",
        "header": "#262d36",
        "gridline": "#2c343e",
        "border": "#3b434e",
        "border_faint": "#2c343e",
        "text": "#e6edf3",
        "muted": "#9da7b3",
        "text_disabled": "#6b7580",
        "pressed": "#2c343e",
        "accent": "#79b8ff",
        "accent_hover": "#94c7ff",
        "accent_pressed": "#5aa3f0",
        "accent_disabled": "#3d5570",
        "on_accent": "#0b1520",
        "danger": "#ff7b81",
        "danger_hover": "#ff959a",
        "log_heading": "#79b8ff",
        "log_info": "#e6edf3",
        "log_debug": "#9da7b3",
        "log_success": "#56d364",
        "log_warning": "#e3b341",
        "log_error": "#ff7b81",
    },
}

#: Pairs that carry text, as (foreground, background) keys. What
#: `tools/contrast.py` and the tests measure. Disabled text is left out:
#: WCAG exempts it, and it is meant to recede.
TEXT_PAIRS = (
    ("text", "background"),
    ("text", "panel"),
    ("text", "panel_sunken"),
    ("text", "header"),
    ("muted", "background"),
    ("muted", "panel"),
    ("muted", "panel_sunken"),
    ("accent", "panel"),
    ("accent", "background"),
    ("on_accent", "accent"),
    ("on_accent", "accent_hover"),
    ("on_accent", "accent_pressed"),
    ("on_accent", "danger"),
    ("on_accent", "danger_hover"),
    ("log_heading", "panel"),
    ("log_info", "panel"),
    ("log_debug", "panel"),
    ("log_success", "panel"),
    ("log_warning", "panel"),
    ("log_error", "panel"),
)


def stylesheet(palette: Dict[str, str]) -> str:
    """The whole stylesheet, from one palette. Nothing in it names a colour."""
    return f"""
QWidget {{
    background-color: {palette['background']};
    color: {palette['text']};
    font-size: 10pt;
}}

QTabWidget::pane {{
    border: 1px solid {palette['border']};
    border-radius: 6px;
    background-color: {palette['panel']};
    top: -1px;
}}

QTabBar::tab {{
    background: transparent;
    padding: 8px 18px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    color: {palette['muted']};
}}

QTabBar::tab:selected {{
    background: {palette['panel']};
    border: 1px solid {palette['border']};
    border-bottom-color: {palette['panel']};
    color: {palette['text']};
    font-weight: 600;
}}

QTabBar::tab:hover:!selected {{
    color: {palette['text']};
}}

QGroupBox {{
    background-color: {palette['panel']};
    border: 1px solid {palette['border']};
    border-radius: 6px;
    margin-top: 14px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 5px;
    color: {palette['accent']};
}}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background-color: {palette['panel']};
    border: 1px solid {palette['border']};
    border-radius: 4px;
    padding: 5px 7px;
    selection-background-color: {palette['accent']};
    selection-color: {palette['on_accent']};
}}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {palette['accent']};
}}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    background-color: {palette['panel_sunken']};
    color: {palette['text_disabled']};
}}

QComboBox QAbstractItemView {{
    background-color: {palette['panel']};
    color: {palette['text']};
    border: 1px solid {palette['border']};
    selection-background-color: {palette['accent']};
    selection-color: {palette['on_accent']};
}}

QPushButton {{
    background-color: {palette['panel']};
    border: 1px solid {palette['border']};
    border-radius: 5px;
    padding: 7px 16px;
}}

QPushButton:hover {{ border-color: {palette['accent']}; }}
QPushButton:pressed {{ background-color: {palette['pressed']}; }}
QPushButton:disabled {{ color: {palette['text_disabled']}; border-color: {palette['border_faint']}; }}

QPushButton#primaryButton {{
    background-color: {palette['accent']};
    color: {palette['on_accent']};
    border: none;
    font-weight: 600;
    padding: 10px 26px;
}}
QPushButton#primaryButton:hover {{ background-color: {palette['accent_hover']}; }}
QPushButton#primaryButton:pressed {{ background-color: {palette['accent_pressed']}; }}
QPushButton#primaryButton:disabled {{ background-color: {palette['accent_disabled']}; color: {palette['pressed']}; }}

QPushButton#stopButton {{
    background-color: {palette['danger']};
    color: {palette['on_accent']};
    border: none;
    font-weight: 600;
    padding: 10px 26px;
}}
QPushButton#stopButton:hover {{ background-color: {palette['danger_hover']}; }}

QProgressBar {{
    border: 1px solid {palette['border']};
    border-radius: 5px;
    background-color: {palette['panel']};
    height: 20px;
    text-align: center;
    color: {palette['text']};
}}

QProgressBar::chunk {{
    background-color: {palette['accent']};
    border-radius: 4px;
}}

QTextEdit#logView, QTextEdit#checkList {{
    background-color: {palette['panel']};
    border: 1px solid {palette['border']};
    border-radius: 6px;
    padding: 8px;
}}

QLabel#sectionLabel {{
    color: {palette['accent']};
    font-weight: 700;
    padding-top: 10px;
}}

QLabel#helpLabel {{
    color: {palette['muted']};
    font-size: 9pt;
    padding-bottom: 4px;
}}

QLabel#statusLabel {{
    color: {palette['muted']};
}}

/* What pressing Start would do. Set apart from the controls around it,
   because it is a statement rather than something to fill in - and given a
   little more weight than help text, since it is the last thing read before
   committing to a run that may take an hour. */
QLabel#planLabel {{
    color: {palette['text']};
    background-color: {palette['panel_sunken']};
    border-left: 3px solid {palette['accent']};
    border-radius: 3px;
    padding: 7px 10px;
    margin: 2px 0px 4px 0px;
}}

QLabel#computedValue {{
    color: {palette['muted']};
    font-family: Consolas, Menlo, monospace;
    background-color: {palette['panel_sunken']};
    border-radius: 3px;
    padding: 4px 6px;
}}

QLabel#titleLabel {{
    font-size: 16pt;
    font-weight: 700;
    color: {palette['accent']};
}}

QTableWidget, QTableView {{
    background-color: {palette['panel']};
    border: 1px solid {palette['border']};
    border-radius: 6px;
    gridline-color: {palette['gridline']};
}}

QHeaderView::section {{
    background-color: {palette['header']};
    border: none;
    border-right: 1px solid {palette['border']};
    border-bottom: 1px solid {palette['border']};
    padding: 6px 8px;
    font-weight: 600;
}}

QFrame#rule {{ color: {palette['border']}; }}

QCheckBox {{ spacing: 8px; padding: 3px 0; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* Fusion paints scroll bars from its own greys, not the palette, so they
   stayed light on the first dark window. Styled flat, with no arrows: the
   bar is the least interesting thing on the page and should look it. */
QScrollBar:vertical {{ background: {palette['background']}; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: {palette['background']}; height: 12px; margin: 0; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {palette['border']};
    border-radius: 4px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{ min-height: 24px; }}
QScrollBar::handle:horizontal {{ min-width: 24px; }}
QScrollBar::handle:hover {{ background: {palette['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenuBar {{ background-color: {palette['background']}; }}
QMenuBar::item:selected {{ background-color: {palette['panel_sunken']}; }}
QMenu {{
    background-color: {palette['panel']};
    border: 1px solid {palette['border']};
}}
QMenu::item:selected {{
    background-color: {palette['accent']};
    color: {palette['on_accent']};
}}

QToolTip {{
    background-color: {palette['panel']};
    color: {palette['text']};
    border: 1px solid {palette['border']};
    padding: 4px;
}}
"""


# ------------------------------------------------------------- the choice


def system_scheme() -> str:
    """
    What the operating system prefers, `LIGHT` when it will not say.

    Qt reads this on Windows 10 and later and on macOS; on a Linux desktop
    it depends on the portal, and the honest answer when nothing is known
    is the scheme every user has seen before.
    """
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication

        hints = QGuiApplication.styleHints()
        if hints is not None and hints.colorScheme() == Qt.ColorScheme.Dark:
            return DARK
    except Exception:            # noqa: BLE001 - a hint, not a dependency
        pass
    return LIGHT


def resolve(choice: Optional[str]) -> str:
    """
    Turn what the user asked for into the scheme that will be drawn.

    Anything unrecognised - a hand-edited value, a setting from a version
    that had more choices - is treated as `SYSTEM`, because the alternative
    is a window that refuses to open over a preference.
    """
    if choice in (LIGHT, DARK):
        return choice
    return system_scheme()


#: Where the choice is kept: `QSettings`, per user, beside the library-offer
#: flag. Not the YAML settings file - that describes a project and gets
#: handed round a lab, and one person's eyes are not another's.
SETTING_KEY = "display/scheme"


def saved_choice() -> str:
    """What the user asked for last time, `SYSTEM` if they never said."""
    from PyQt6.QtCore import QSettings

    value = QSettings().value(SETTING_KEY, SYSTEM, type=str)
    return value if value in CHOICES else SYSTEM


def save_choice(choice: str) -> None:
    from PyQt6.QtCore import QSettings

    if choice not in CHOICES:
        raise ValueError(f"not a choice: {choice!r}")
    QSettings().setValue(SETTING_KEY, choice)


# ---------------------------------------------------------------- applying

_active: str = LIGHT


def active() -> Dict[str, str]:
    """The palette currently on screen. Widgets that paint ask this."""
    return PALETTES[_active]


def active_scheme() -> str:
    return _active


def qt_palette(palette: Dict[str, str]):
    """
    A `QPalette` agreeing with the stylesheet, for code that asks Qt.

    Only the roles something in this program reads. Everything else is left
    to Fusion, which derives sensible values from these.
    """
    from PyQt6.QtGui import QColor, QPalette

    result = QPalette()
    roles = {
        QPalette.ColorRole.Window: "background",
        QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "panel",
        QPalette.ColorRole.AlternateBase: "panel_sunken",
        QPalette.ColorRole.Text: "text",
        QPalette.ColorRole.Button: "panel",
        QPalette.ColorRole.ButtonText: "text",
        QPalette.ColorRole.ToolTipBase: "panel",
        QPalette.ColorRole.ToolTipText: "text",
        QPalette.ColorRole.PlaceholderText: "muted",
        QPalette.ColorRole.Highlight: "accent",
        QPalette.ColorRole.HighlightedText: "on_accent",
    }
    for role, key in roles.items():
        result.setColor(role, QColor(palette[key]))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
    ):
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(palette["text_disabled"]))
    return result


def apply(app, scheme: str) -> Dict[str, str]:
    """
    Put a scheme on screen: stylesheet and palette together, then remember
    which one, so that widgets painting afterwards agree with it.

    Returns the palette, for callers that want to repaint something the
    stylesheet does not reach - the log's existing lines, say.
    """
    global _active
    if scheme not in PALETTES:
        raise ValueError(f"not a scheme: {scheme!r}")
    palette = PALETTES[scheme]
    app.setPalette(qt_palette(palette))
    app.setStyleSheet(stylesheet(palette))
    _active = scheme
    return palette

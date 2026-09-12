# src/gui/theme.py
"""
The application's visual style: light, dark and high contrast, at three
text sizes, in the system face or a dyslexia-friendly one.

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

#: What a user can ask for. `SYSTEM` follows the operating system's own
#: light-or-dark preference, and is the default so that a first run looks
#: like everything else on the desktop. High contrast is only ever chosen.
SYSTEM, LIGHT, DARK, HIGH_CONTRAST = "system", "light", "dark", "high-contrast"
CHOICES = (SYSTEM, LIGHT, DARK, HIGH_CONTRAST)

#: Text sizes, as the base point size everything else is measured from.
#: Three steps because that is what people have met elsewhere - Windows,
#: the browsers, the phone - and a slider invites tuning a number that was
#: never the point. Small is for a large monitor; large is for a laptop at
#: arm's length, or eyes that are not what they were.
SMALL, NORMAL, LARGE = "small", "normal", "large"
SIZES = {SMALL: 9, NORMAL: 10, LARGE: 12}
SIZE_CHOICES = (SMALL, NORMAL, LARGE)

#: The dyslexia-friendly face. OpenDyslexic, under the SIL Open Font
#: Licence, bundled in `resources/fonts/` with its licence beside it. Named
#: here once so that the stylesheet and the loader cannot disagree about it.
DYSLEXIC_FAMILY = "OpenDyslexic"
DYSLEXIC_FILES = ("OpenDyslexic-Regular.otf", "OpenDyslexic-Bold.otf")

# Colours are deliberately low-contrast except where something needs
# attention, so that a warning in the log stands out on its own. Every
# text-on-surface pair in both schemes meets WCAG AA (4.5:1) and
# `tools/contrast.py` measures that - eyeballing a dark scheme is how a grey
# ends up unreadable on a laptop in daylight.
#
# The ground - `background`, the colour behind everything - is TaxaTag's
# blue, the blue of the base pairs in the emblem: a light steel blue in the
# light scheme, navy in the dark. The panels, fields, tables and the log
# stay white or near-black, so the blue frames the work without touching
# anything that carries meaning (decisions/0025). The secondary surfaces
# take the same tint so the scheme reads as one thing.
PALETTES: Dict[str, Dict[str, str]] = {
    LIGHT: {
        "background": "#cfdcec",
        "panel": "#ffffff",
        "panel_sunken": "#e9eff6",      # disabled fields, the plan box
        "header": "#e3ebf4",            # table column headings
        "gridline": "#e3eaf2",
        "border": "#c3d1e1",
        "border_faint": "#dbe4ee",
        "text": "#24292f",
        "muted": "#4c555f",
        "text_disabled": "#9aa1a9",
        "pressed": "#e3ebf4",
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
        "background": "#0c1a2e",
        "panel": "#16243a",
        "panel_sunken": "#1e2e47",
        "header": "#1e2e47",
        "gridline": "#27395a",
        "border": "#33466a",
        "border_faint": "#27395a",
        "text": "#e6edf3",
        "muted": "#9da7b3",
        "text_disabled": "#6b7580",
        "pressed": "#27395a",
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
    # Not a toggle on the other two: a scheme of its own, for people who
    # need all of it. Black on white, no muted greys - the help text is as
    # black as the fields - and every border drawn at twice the weight
    # (`BORDER_WIDTH`). The log's colours are darkened until each clears
    # 7:1, AAA, since severity here is carried by the [OK] / [!] prefix and
    # the colour is only a second signal.
    HIGH_CONTRAST: {
        "background": "#ffffff",
        "panel": "#ffffff",
        "panel_sunken": "#f0f0f0",
        "header": "#e6e6e6",
        "gridline": "#000000",
        "border": "#000000",
        "border_faint": "#000000",
        "text": "#000000",
        "muted": "#000000",
        "text_disabled": "#595959",
        "pressed": "#d9d9d9",
        "accent": "#00308a",
        "accent_hover": "#0044b3",
        "accent_pressed": "#001f5c",
        "accent_disabled": "#6b6b6b",
        "on_accent": "#ffffff",
        "danger": "#8f0000",
        "danger_hover": "#a60000",
        "log_heading": "#00308a",
        "log_info": "#000000",
        "log_debug": "#000000",
        "log_success": "#005c00",
        "log_warning": "#6e4300",
        "log_error": "#8f0000",
    },
}

#: Schemes held to AAA (7:1) rather than AA. High contrast exists for
#: people for whom 4.5:1 is not enough, so 4.5:1 is not the bar for it.
AAA_SCHEMES = (HIGH_CONTRAST,)

#: Border weight per scheme, in pixels. A colour cannot say "heavier".
BORDER_WIDTH = {LIGHT: 1, DARK: 1, HIGH_CONTRAST: 2}

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


def stylesheet(palette: Dict[str, str], size: str = NORMAL, dyslexic: bool = False,
               border: int = 1) -> str:
    """
    The whole stylesheet, from one palette and one text size. Nothing in it
    names a colour, and every point size is measured from `base`, so that
    the help text stays a step smaller than the fields and the title a
    step larger whichever size is chosen.

    The monospace log keeps its face unless the dyslexia-friendly font is
    on, in which case readability wins over alignment: a progress log is
    lines, not columns, and it is the thing read most during a run.
    """
    base = SIZES[size]
    px = f"{border}px"
    family = f'font-family: "{DYSLEXIC_FAMILY}";' if dyslexic else ""
    log_family = f'"{DYSLEXIC_FAMILY}"' if dyslexic else "Consolas, Menlo, monospace"
    return f"""
QWidget {{
    background-color: {palette['background']};
    color: {palette['text']};
    font-size: {base}pt;
    {family}
}}

QTabWidget::pane {{
    border: {px} solid {palette['border']};
    border-radius: 6px;
    background-color: {palette['panel']};
    top: -1px;
}}

QTabBar::tab {{
    background: transparent;
    padding: 8px 18px;
    margin-right: 2px;
    border: {px} solid transparent;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    color: {palette['muted']};
}}

QTabBar::tab:selected {{
    background: {palette['panel']};
    border: {px} solid {palette['border']};
    border-bottom-color: {palette['panel']};
    color: {palette['text']};
    font-weight: 600;
}}

QTabBar::tab:hover:!selected {{
    color: {palette['text']};
}}

QGroupBox {{
    background-color: {palette['panel']};
    border: {px} solid {palette['border']};
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
    border: {px} solid {palette['border']};
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

/* A spin box's buttons default to a width the frame and padding leave no
   arrow room in, so v1.0.0 shipped them as blank rectangles in both
   schemes. Only the width is set: a button given any paint of its own
   draws nothing it is not handed an image for, arrows included. */
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {palette['panel']};
    color: {palette['text']};
    border: {px} solid {palette['border']};
    selection-background-color: {palette['accent']};
    selection-color: {palette['on_accent']};
}}

QPushButton {{
    background-color: {palette['panel']};
    border: {px} solid {palette['border']};
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
    border: {px} solid {palette['border']};
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
    border: {px} solid {palette['border']};
    border-radius: 6px;
    padding: 8px;
}}

QTextEdit#logView {{
    font-family: {log_family};
    font-size: {base - 1}pt;
}}

QLabel#sectionLabel {{
    color: {palette['accent']};
    font-weight: 700;
    padding-top: 10px;
}}

QLabel#helpLabel {{
    color: {palette['muted']};
    font-size: {base - 1}pt;
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
    font-family: {log_family};
    background-color: {palette['panel_sunken']};
    border-radius: 3px;
    padding: 4px 6px;
}}

QLabel#titleLabel {{
    font-size: {base + 6}pt;
    font-weight: 700;
    color: {palette['accent']};
}}

QTableWidget, QTableView {{
    background-color: {palette['panel']};
    border: {px} solid {palette['border']};
    border-radius: 6px;
    gridline-color: {palette['gridline']};
}}

QHeaderView::section {{
    background-color: {palette['header']};
    border: none;
    border-right: {px} solid {palette['border']};
    border-bottom: {px} solid {palette['border']};
    padding: 6px 8px;
    font-weight: 600;
}}

QFrame#rule {{ color: {palette['border']}; }}

QCheckBox {{ spacing: 8px; padding: 3px 0; }}

/* Text sits on whatever is behind it. The QWidget rule above gives every
   widget the ground colour, and a label inside a white panel was painting
   a strip of ground across it - faint grey until the ground was blue. The
   labels that carry their own box (the plan, computed values) set a
   background of their own further down and win by specificity. */
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}

/* Fusion outlines a check box with the window colour darkened, which on a
   dark window is darker than the dark: the box vanished from the primer
   table and from the warning that offers "don't ask again". Only the
   unticked state is styled. Styling the ticked one too would take the
   tick away as well, since a styled indicator draws nothing it is not
   given an image for - and the tick reads on its own. */
QCheckBox::indicator:unchecked, QTableView::indicator:unchecked {{
    width: 14px;
    height: 14px;
    border: {px} solid {palette['muted']};
    border-radius: 3px;
    background: {palette['panel']};
}}

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
    border: {px} solid {palette['border']};
}}
QMenu::item:selected {{
    background-color: {palette['accent']};
    color: {palette['on_accent']};
}}

QToolTip {{
    background-color: {palette['panel']};
    color: {palette['text']};
    border: {px} solid {palette['border']};
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
    if choice in PALETTES:
        return choice
    return system_scheme()


#: Where the choices are kept: `QSettings`, per user, beside the
#: library-offer flag. Not the YAML settings file - that describes a project
#: and gets handed round a lab, and one person's eyes are not another's.
SETTING_KEY = "display/scheme"
SIZE_KEY = "display/text_size"
DYSLEXIC_KEY = "display/dyslexic_font"


def _saved(key: str, default: str, allowed) -> str:
    from PyQt6.QtCore import QSettings

    value = QSettings().value(key, default, type=str)
    return value if value in allowed else default


def _save(key: str, value: str, allowed) -> None:
    from PyQt6.QtCore import QSettings

    if value not in allowed:
        raise ValueError(f"not a choice for {key}: {value!r}")
    QSettings().setValue(key, value)


def saved_choice() -> str:
    """What the user asked for last time, `SYSTEM` if they never said."""
    return _saved(SETTING_KEY, SYSTEM, CHOICES)


def save_choice(choice: str) -> None:
    _save(SETTING_KEY, choice, CHOICES)


def saved_size() -> str:
    return _saved(SIZE_KEY, NORMAL, SIZE_CHOICES)


def save_size(size: str) -> None:
    _save(SIZE_KEY, size, SIZE_CHOICES)


def saved_dyslexic() -> bool:
    from PyQt6.QtCore import QSettings

    return QSettings().value(DYSLEXIC_KEY, False, type=bool)


def save_dyslexic(on: bool) -> None:
    from PyQt6.QtCore import QSettings

    QSettings().setValue(DYSLEXIC_KEY, bool(on))


# ---------------------------------------------------------------- applying

_active: str = LIGHT
_size: str = NORMAL
_dyslexic: bool = False
_fonts_loaded: bool = False


def load_fonts() -> bool:
    """
    Register the bundled faces with Qt, once. False when they are not there
    - a source checkout missing the folder - in which case the stylesheet
    still names the family and Qt substitutes, which is a plain window
    rather than a broken one.
    """
    global _fonts_loaded
    if _fonts_loaded:
        return True
    from PyQt6.QtGui import QFontDatabase

    from src.utils import platform as platform_utils

    folder = platform_utils.app_root() / "resources" / "fonts"
    found = 0
    for name in DYSLEXIC_FILES:
        path = folder / name
        if path.exists() and QFontDatabase.addApplicationFont(str(path)) >= 0:
            found += 1
    _fonts_loaded = found == len(DYSLEXIC_FILES)
    return _fonts_loaded


def active_size() -> str:
    return _size


def active_dyslexic() -> bool:
    return _dyslexic


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


def apply(app, scheme: str, size: Optional[str] = None,
          dyslexic: Optional[bool] = None) -> Dict[str, str]:
    """
    Put a scheme on screen: stylesheet and palette together, then remember
    which one, so that widgets painting afterwards agree with it. `size`
    and `dyslexic` left as None keep whatever was last applied.

    Returns the palette, for callers that want to repaint something the
    stylesheet does not reach - the log's existing lines, say.
    """
    global _active, _size, _dyslexic
    if scheme not in PALETTES:
        raise ValueError(f"not a scheme: {scheme!r}")
    if size is not None:
        if size not in SIZES:
            raise ValueError(f"not a text size: {size!r}")
        _size = size
    if dyslexic is not None:
        _dyslexic = bool(dyslexic)
        if _dyslexic:
            load_fonts()
    palette = PALETTES[scheme]
    app.setPalette(qt_palette(palette))
    app.setStyleSheet(stylesheet(palette, _size, _dyslexic, BORDER_WIDTH[scheme]))
    _active = scheme
    return palette

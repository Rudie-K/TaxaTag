# src/gui/theme.py
"""
The application's visual style.

Qt's default look differs noticeably between Windows, macOS and Linux. A
single stylesheet keeps the tool recognisable in a screenshot taken on any of
them, which matters for a program whose documentation has to serve all three.
"""

from __future__ import annotations

# Colours are deliberately low-contrast except where something needs
# attention, so that a warning in the log stands out on its own.
COLOURS = {
    "background": "#f6f7f9",
    "panel": "#ffffff",
    "border": "#d8dce1",
    "text": "#24292f",
    "muted": "#57606a",
    "accent": "#1a4d7a",
    "accent_hover": "#215f96",
    "accent_pressed": "#143c60",
    "danger": "#b21f2d",
    "danger_hover": "#c8303e",
}

STYLESHEET = f"""
QWidget {{
    background-color: {COLOURS['background']};
    color: {COLOURS['text']};
    font-size: 10pt;
}}

QTabWidget::pane {{
    border: 1px solid {COLOURS['border']};
    border-radius: 6px;
    background-color: {COLOURS['panel']};
    top: -1px;
}}

QTabBar::tab {{
    background: transparent;
    padding: 8px 18px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    color: {COLOURS['muted']};
}}

QTabBar::tab:selected {{
    background: {COLOURS['panel']};
    border: 1px solid {COLOURS['border']};
    border-bottom-color: {COLOURS['panel']};
    color: {COLOURS['text']};
    font-weight: 600;
}}

QTabBar::tab:hover:!selected {{
    color: {COLOURS['text']};
}}

QGroupBox {{
    background-color: {COLOURS['panel']};
    border: 1px solid {COLOURS['border']};
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
    color: {COLOURS['accent']};
}}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background-color: {COLOURS['panel']};
    border: 1px solid {COLOURS['border']};
    border-radius: 4px;
    padding: 5px 7px;
    selection-background-color: {COLOURS['accent']};
    selection-color: #ffffff;
}}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {COLOURS['accent']};
}}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    background-color: #f0f1f3;
    color: #9aa1a9;
}}

QPushButton {{
    background-color: {COLOURS['panel']};
    border: 1px solid {COLOURS['border']};
    border-radius: 5px;
    padding: 7px 16px;
}}

QPushButton:hover {{ border-color: {COLOURS['accent']}; }}
QPushButton:pressed {{ background-color: #eef2f6; }}
QPushButton:disabled {{ color: #9aa1a9; border-color: #e6e8eb; }}

QPushButton#primaryButton {{
    background-color: {COLOURS['accent']};
    color: #ffffff;
    border: none;
    font-weight: 600;
    padding: 10px 26px;
}}
QPushButton#primaryButton:hover {{ background-color: {COLOURS['accent_hover']}; }}
QPushButton#primaryButton:pressed {{ background-color: {COLOURS['accent_pressed']}; }}
QPushButton#primaryButton:disabled {{ background-color: #9fb3c4; color: #eef2f6; }}

QPushButton#stopButton {{
    background-color: {COLOURS['danger']};
    color: #ffffff;
    border: none;
    font-weight: 600;
    padding: 10px 26px;
}}
QPushButton#stopButton:hover {{ background-color: {COLOURS['danger_hover']}; }}

QProgressBar {{
    border: 1px solid {COLOURS['border']};
    border-radius: 5px;
    background-color: {COLOURS['panel']};
    height: 20px;
    text-align: center;
    color: {COLOURS['text']};
}}

QProgressBar::chunk {{
    background-color: {COLOURS['accent']};
    border-radius: 4px;
}}

QTextEdit#logView, QTextEdit#checkList {{
    background-color: {COLOURS['panel']};
    border: 1px solid {COLOURS['border']};
    border-radius: 6px;
    padding: 8px;
}}

QLabel#sectionLabel {{
    color: {COLOURS['accent']};
    font-weight: 700;
    padding-top: 10px;
}}

QLabel#helpLabel {{
    color: {COLOURS['muted']};
    font-size: 9pt;
    padding-bottom: 4px;
}}

QLabel#statusLabel {{
    color: {COLOURS['muted']};
}}

/* What pressing Start would do. Set apart from the controls around it,
   because it is a statement rather than something to fill in - and given a
   little more weight than help text, since it is the last thing read before
   committing to a run that may take an hour. Colours come from the palette
   so that a change of scheme carries here without an edit. */
QLabel#planLabel {{
    color: {COLOURS['text']};
    background-color: #f0f1f3;
    border-left: 3px solid {COLOURS['accent']};
    border-radius: 3px;
    padding: 7px 10px;
    margin: 2px 0px 4px 0px;
}}

QLabel#computedValue {{
    color: {COLOURS['muted']};
    font-family: Consolas, Menlo, monospace;
    background-color: #f0f1f3;
    border-radius: 3px;
    padding: 4px 6px;
}}

QLabel#titleLabel {{
    font-size: 16pt;
    font-weight: 700;
    color: {COLOURS['accent']};
}}

QTableWidget, QTableView {{
    background-color: {COLOURS['panel']};
    border: 1px solid {COLOURS['border']};
    border-radius: 6px;
    gridline-color: #eceef0;
}}

QHeaderView::section {{
    background-color: #eef1f4;
    border: none;
    border-right: 1px solid {COLOURS['border']};
    border-bottom: 1px solid {COLOURS['border']};
    padding: 6px 8px;
    font-weight: 600;
}}

QFrame#rule {{ color: {COLOURS['border']}; }}

QCheckBox {{ spacing: 8px; padding: 3px 0; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""

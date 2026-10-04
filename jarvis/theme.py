"""Colours, fonts and the Qt style sheet."""

import sys

if sys.platform == "win32":
    FONT_FAMILY, MONO_FAMILY = "Bahnschrift", "Consolas"
elif sys.platform == "darwin":
    FONT_FAMILY, MONO_FAMILY = "Avenir Next Condensed", "Menlo"
else:
    FONT_FAMILY, MONO_FAMILY = "DejaVu Sans", "DejaVu Sans Mono"

CYAN = "#3fd8ff"
ICE = "#d9f7ff"
AMBER = "#ffb648"
RED = "#ff4d5a"
GREEN = "#5cffb0"
TEXT = "#bfeeff"
MUTED = "rgba(191, 238, 255, 0.5)"

STATE_ACCENT = {"thinking": AMBER, "transcribing": AMBER, "error": RED}

STYLE = f"""
* {{ font-family: "{FONT_FAMILY}"; color: {TEXT}; }}
QToolTip {{ background: #04121e; color: {ICE}; border: 1px solid rgba(63,216,255,0.4); }}

#titleBar {{ background: rgba(4, 18, 30, 0.8); border-bottom: 1px solid rgba(63,216,255,0.12); }}
#brandName {{ color: {ICE}; font-size: 15px; }}
#brandSub {{ color: {MUTED}; font-size: 11px; }}
#winButton {{ background: transparent; border: 1px solid transparent; color: rgba(63,216,255,0.75);
              font-size: 14px; min-width: 38px; min-height: 28px; }}
#winButton:hover {{ border-color: rgba(63,216,255,0.3); background: rgba(63,216,255,0.1); color: {ICE}; }}
#closeButton {{ background: transparent; border: 1px solid transparent; color: rgba(63,216,255,0.75);
                font-size: 14px; min-width: 38px; min-height: 28px; }}
#closeButton:hover {{ background: rgba(255,77,90,0.35); color: white; }}

#panelTitle {{ color: {CYAN}; font-size: 11px; }}
#clock {{ color: {ICE}; font-size: 50px; }}
#clockSub {{ color: {MUTED}; font-family: "{MONO_FAMILY}"; font-size: 12px; }}
#bigValue {{ color: {ICE}; font-size: 38px; }}
#key {{ color: {MUTED}; font-size: 10px; }}
#value {{ font-family: "{MONO_FAMILY}"; font-size: 12px; }}
#muted {{ color: {MUTED}; font-size: 12px; }}

#status {{ font-size: 15px; }}
#caption {{ color: {ICE}; font-size: 19px; }}
#hint {{ color: {MUTED}; font-size: 11px; }}

#logArea {{ background: transparent; border: none; }}
#logArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 5px; }}
QScrollBar::handle:vertical {{ background: rgba(63,216,255,0.3); min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
#logWho {{ font-size: 10px; color: {MUTED}; }}
#logText {{ font-size: 14px; }}

#tag {{ font-family: "{MONO_FAMILY}"; font-size: 11px; padding: 1px 8px; border: 1px solid rgba(63,216,255,0.35); color: {CYAN}; }}
#tag[kind="ok"] {{ border-color: rgba(92,255,176,0.45); color: {GREEN}; }}
#tag[kind="warn"] {{ border-color: rgba(255,182,72,0.5); color: {AMBER}; }}
#tag[kind="bad"] {{ border-color: rgba(255,77,90,0.55); color: {RED}; }}

#commandInput {{ background: rgba(4,20,34,0.8); border: 1px solid rgba(63,216,255,0.3); color: {ICE};
                 font-size: 15px; padding: 0 16px; min-height: 40px; selection-background-color: rgba(63,216,255,0.35); }}
#commandInput:focus {{ border-color: {CYAN}; }}
#modeButton {{ background: rgba(4,20,34,0.6); border: 1px solid rgba(63,216,255,0.12); color: {MUTED};
               font-size: 11px; min-height: 40px; padding: 0 16px; }}
#modeButton:hover {{ border-color: rgba(63,216,255,0.35); color: {TEXT}; }}
#modeButton[on="true"] {{ color: {ICE}; border-color: rgba(92,255,176,0.45); }}
#modeButton[on="false"] {{ color: {ICE}; border-color: rgba(255,77,90,0.5); }}

#bootTitle {{ color: {ICE}; font-size: 46px; }}
#bootLine {{ color: rgba(63,216,255,0.8); font-family: "{MONO_FAMILY}"; font-size: 13px; }}

QDialog, #settingsBody {{ background: #030e18; }}
QGroupBox {{ border: 1px solid rgba(63,216,255,0.12); margin-top: 14px; padding: 12px 10px 10px; font-size: 11px; color: rgba(63,216,255,0.8); }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px; }}
QLineEdit, QComboBox, QSpinBox {{ background: rgba(63,216,255,0.05); border: 1px solid rgba(63,216,255,0.3);
                                  color: {ICE}; min-height: 30px; padding: 0 8px; font-size: 13px; }}
QComboBox QAbstractItemView {{ background: #04121e; color: {ICE}; selection-background-color: rgba(63,216,255,0.3); }}
QLabel#formLabel {{ color: {MUTED}; font-size: 12px; }}
QCheckBox {{ font-size: 13px; }}
QSlider::groove:horizontal {{ height: 4px; background: rgba(63,216,255,0.15); }}
QSlider::handle:horizontal {{ background: {CYAN}; width: 12px; margin: -5px 0; }}
QPushButton#primary {{ font-size: 11px; background: {CYAN}; color: #021018; border: none; min-height: 34px; padding: 0 20px; }}
QPushButton#primary:hover {{ background: {ICE}; }}
QPushButton#ghost {{ font-size: 11px; background: transparent; border: 1px solid rgba(63,216,255,0.35); color: {CYAN}; min-height: 30px; padding: 0 14px; }}
QPushButton#ghost:hover {{ background: rgba(63,216,255,0.1); }}
QPushButton#danger {{ font-size: 11px; background: transparent; border: 1px solid rgba(255,77,90,0.5); color: {RED}; min-height: 30px; padding: 0 14px; }}
"""

# Qt style sheets have no letter-spacing, so it is applied to fonts directly.
SPACING = {
    "brandName": 5, "brandSub": 2, "panelTitle": 4, "key": 2, "status": 7, "hint": 3,
    "logWho": 3, "modeButton": 3, "bootTitle": 22, "primary": 3, "ghost": 2, "danger": 2,
}


def apply_spacing(root) -> None:
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QWidget

    widgets = [root, *root.findChildren(QWidget)]
    for w in widgets:
        px = SPACING.get(w.objectName())
        if px:
            font = w.font()
            font.setLetterSpacing(QFont.AbsoluteSpacing, px)
            w.setFont(font)

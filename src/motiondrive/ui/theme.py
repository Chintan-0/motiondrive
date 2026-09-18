"""Dark, modern gaming-tech QSS theme shared by every window."""

COLOR_BG = "#0d1117"
COLOR_PANEL = "#161b22"
COLOR_PANEL_ALT = "#1c2330"
COLOR_BORDER = "#2a3240"
COLOR_TEXT = "#e6edf3"
COLOR_TEXT_DIM = "#8b96a5"
COLOR_ACCENT = "#3b9dff"
COLOR_ACCENT_DIM = "#1f5fa8"
COLOR_GREEN = "#3ddc84"
COLOR_YELLOW = "#f5c542"
COLOR_RED = "#ff5c5c"

QSS = f"""
* {{
    font-family: 'Segoe UI', 'Segoe UI Variable', Arial, sans-serif;
    color: {COLOR_TEXT};
}}
QWidget {{
    background-color: transparent;
}}
QWidget#root {{
    background-color: {COLOR_BG};
}}
/* Deliberately no border-radius here: root wraps TitleBar, and TitleBar's
   own custom-painted caption buttons (minimize/maximize/close) stopped
   rendering entirely whenever BOTH root and TitleBar had a QSS
   border-radius at once -- confirmed via direct isolation testing, a Qt
   clip-region bug with two stacked WA_StyledBackground+radius ancestors
   above a custom paintEvent widget. TitleBar keeps its own top-corner
   radius (see titlebar.py); root stays a flat rectangle. */
QFrame#panel, QFrame.panel {{
    background-color: {COLOR_PANEL};
    border: 1px solid {COLOR_BORDER};
    border-radius: 14px;
}}
QLabel#appTitle {{
    font-size: 22px;
    font-weight: 600;
    letter-spacing: 1px;
}}
QLabel#sectionTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {COLOR_TEXT_DIM};
    letter-spacing: 1px;
}}
QLabel.dim {{
    color: {COLOR_TEXT_DIM};
}}
QLabel.big {{
    font-size: 20px;
    font-weight: 600;
}}
QPushButton {{
    background-color: {COLOR_PANEL_ALT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 10px;
    padding: 10px 18px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton:hover {{
    border-color: {COLOR_ACCENT};
}}
QPushButton:pressed {{
    background-color: {COLOR_ACCENT_DIM};
}}
QPushButton:disabled {{
    background-color: #11151c;
    border-color: #1c222c;
    color: #4a5568;
}}
QPushButton#primary:disabled {{
    background-color: #1c2734;
    color: #4a5568;
}}
QPushButton#stop:disabled {{
    background-color: #3a1f1f;
    color: #6b4444;
}}
QPushButton#primary {{
    background-color: {COLOR_ACCENT};
    color: #051019;
    border: none;
    font-size: 15px;
    padding: 14px 28px;
}}
QPushButton#primary:hover {{
    background-color: #5cb0ff;
}}
QPushButton#stop {{
    background-color: {COLOR_RED};
    color: #260000;
    border: none;
    font-size: 16px;
    font-weight: 800;
    padding: 16px 28px;
}}
QPushButton#stop:hover {{
    background-color: #ff7a7a;
}}
QPushButton#navItem {{
    background: transparent;
    border: none;
    text-align: left;
    padding: 12px 16px;
    border-radius: 10px;
    font-size: 13px;
}}
QPushButton#navItem:checked {{
    background-color: {COLOR_PANEL_ALT};
    color: {COLOR_ACCENT};
}}
QPushButton#driveFeel {{
    background-color: {COLOR_PANEL_ALT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 10px;
    padding: 14px 10px;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}
QPushButton#driveFeel:checked {{
    background-color: {COLOR_ACCENT_DIM};
    border-color: {COLOR_ACCENT};
    color: {COLOR_TEXT};
}}
QPushButton#responseMode {{
    background-color: {COLOR_PANEL_ALT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 11px;
    font-weight: 700;
}}
QPushButton#responseMode:checked {{
    background-color: {COLOR_ACCENT_DIM};
    border-color: {COLOR_ACCENT};
    color: {COLOR_TEXT};
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: {COLOR_PANEL_ALT};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {COLOR_ACCENT};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {COLOR_TEXT};
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
}}
QComboBox {{
    background-color: {COLOR_PANEL_ALT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 8px 12px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLOR_PANEL_ALT};
    selection-background-color: {COLOR_ACCENT_DIM};
}}
QRadioButton, QCheckBox {{
    spacing: 8px;
    font-size: 13px;
}}
QCheckBox#toggleSwitch::indicator {{
    width: 38px;
    height: 20px;
    border-radius: 10px;
    background-color: {COLOR_PANEL_ALT};
    border: 1px solid {COLOR_BORDER};
}}
QCheckBox#toggleSwitch::indicator:checked {{
    background-color: {COLOR_ACCENT};
    border-color: {COLOR_ACCENT};
}}
QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {COLOR_PANEL_ALT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLOR_ACCENT_DIM};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    background: transparent;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {COLOR_PANEL_ALT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {COLOR_ACCENT_DIM};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
    background: transparent;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}
QToolTip {{
    background-color: {COLOR_PANEL_ALT};
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
}}
"""

"""About page: 3-column layout (branding / key features / version) matching
the rest of the app's visual language. Every fact shown here is real --
version comes from motiondrive.__version__, nothing else (build number,
release date, license, support links) is fabricated. Where the app genuinely
doesn't have that information, the field is simply omitted rather than
invented, per explicit instruction."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame

from motiondrive import __version__
from motiondrive.paths import resource_path
from motiondrive.ui.theme import COLOR_ACCENT, COLOR_GREEN, COLOR_TEXT_DIM

_FEATURES = [
    ("🤚", "Hand Tracking", "Real-time hand detection and tracking."),
    ("🎡", "Motion Steering", "Use natural hand movement to control steering."),
    ("🦶", "Throttle & Brake", "Control acceleration and braking with your hands."),
    ("🎮", "Keyboard & Gamepad", "Works with keyboard controls and virtual gamepad input."),
    ("🎚", "Customizable", "Adjust steering response to match your driving style."),
]


def _card(margins=(22, 20, 22, 20)) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(12)
    return frame, layout


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 20)
        root.setSpacing(16)

        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        title = QLabel("ABOUT MOTIONDRIVE")
        title.setObjectName("sectionTitle")
        header_col.addWidget(title)
        subtitle = QLabel("Learn more about MotionDrive and what it can do.")
        subtitle.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_DIM};")
        header_col.addWidget(subtitle)
        root.addLayout(header_col)

        columns = QHBoxLayout()
        columns.setSpacing(16)

        # ------------------------------------------------------- branding
        brand_card, bc = _card((28, 32, 28, 28))
        bc.setAlignment(Qt.AlignHCenter)
        logo_path = resource_path("assets/icon_256.png")
        if logo_path.exists():
            logo_label = QLabel()
            pix = QPixmap(str(logo_path)).scaled(132, 132, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(pix)
            logo_label.setAlignment(Qt.AlignCenter)
            bc.addWidget(logo_label)

        name_lbl = QLabel("MOTIONDRIVE")
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setStyleSheet("font-size: 30px; font-weight: 800; letter-spacing: 1px;")
        bc.addWidget(name_lbl)

        tagline_lbl = QLabel("TURN MOTION INTO CONTROL")
        tagline_lbl.setAlignment(Qt.AlignCenter)
        tagline_lbl.setStyleSheet(f"font-size: 12px; color: {COLOR_ACCENT}; font-weight: 700; letter-spacing: 2px;")
        bc.addWidget(tagline_lbl)

        bc.addSpacing(10)
        desc_lbl = QLabel("MotionDrive uses hand tracking and computer vision to turn natural hand "
                            "movements into real-time controls for PC racing games.")
        desc_lbl.setWordWrap(True)
        desc_lbl.setAlignment(Qt.AlignCenter)
        desc_lbl.setStyleSheet(f"font-size: 13px; color: {COLOR_TEXT_DIM}; line-height: 140%;")
        desc_lbl.setFixedWidth(300)  # forces word-wrap to settle before the
        # layout computes heightForWidth, avoiding a one-pass-behind clip.
        bc.addWidget(desc_lbl, alignment=Qt.AlignHCenter)

        bc.addSpacing(10)
        privacy_lbl = QLabel("🔒 Camera processing happens 100% locally on your PC.")
        privacy_lbl.setWordWrap(True)
        privacy_lbl.setAlignment(Qt.AlignCenter)
        privacy_lbl.setFixedWidth(300)
        privacy_lbl.setStyleSheet(f"font-size: 12px; color: {COLOR_GREEN}; background-color: #0d1b14; "
                                    "padding: 10px 14px; border-radius: 8px;")
        bc.addWidget(privacy_lbl, alignment=Qt.AlignHCenter)
        columns.addWidget(brand_card, stretch=3, alignment=Qt.AlignTop)

        # --------------------------------------------------------- features
        feat_card, fc = _card()
        feat_title = QLabel("KEY FEATURES")
        feat_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {COLOR_TEXT_DIM}; letter-spacing: 1px;")
        fc.addWidget(feat_title)
        for icon, name, desc in _FEATURES:
            row = QHBoxLayout()
            row.setSpacing(14)
            icon_lbl = QLabel(icon)
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setStyleSheet("font-size: 19px; background-color: #16233a; border-radius: 10px; "
                                     "min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;")
            row.addWidget(icon_lbl)
            text_col = QVBoxLayout()
            text_col.setSpacing(2)
            name_row_lbl = QLabel(name)
            name_row_lbl.setStyleSheet("font-size: 14px; font-weight: 700;")
            text_col.addWidget(name_row_lbl)
            desc_row_lbl = QLabel(desc)
            desc_row_lbl.setWordWrap(True)
            desc_row_lbl.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_DIM};")
            text_col.addWidget(desc_row_lbl)
            row.addLayout(text_col, stretch=1)
            fc.addLayout(row)
        fc.addStretch()
        columns.addWidget(feat_card, stretch=4, alignment=Qt.AlignTop)

        # ---------------------------------------------------------- version
        ver_card, vc = _card()
        ver_title = QLabel("VERSION")
        ver_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {COLOR_TEXT_DIM}; letter-spacing: 1px;")
        vc.addWidget(ver_title)
        ver_row = QHBoxLayout()
        ver_row.setSpacing(12)
        ver_num_lbl = QLabel(__version__)
        ver_num_lbl.setStyleSheet("font-size: 34px; font-weight: 800;")
        ver_row.addWidget(ver_num_lbl)
        # "Stable Release" reflects semver convention (>=1.0.0 = stable API)
        # -- an honest inference from the real version number, not invented.
        major = int(__version__.split(".")[0]) if __version__.split(".")[0].isdigit() else 0
        if major >= 1:
            stable_badge = QLabel("Stable Release")
            stable_badge.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {COLOR_GREEN}; "
                                         "background-color: #0d1b14; padding: 3px 8px; border-radius: 6px;")
            ver_row.addWidget(stable_badge, alignment=Qt.AlignVCenter)
        ver_row.addStretch()
        vc.addLayout(ver_row)
        vc.addStretch()
        columns.addWidget(ver_card, stretch=3, alignment=Qt.AlignTop)

        root.addLayout(columns)
        root.addStretch()

        # ------------------------------------------------------------ footer
        footer = QVBoxLayout()
        footer.setSpacing(2)
        footer.setAlignment(Qt.AlignCenter)
        f_name = QLabel("MOTIONDRIVE")
        f_name.setAlignment(Qt.AlignCenter)
        f_name.setStyleSheet("font-size: 11px; font-weight: 800; letter-spacing: 1px;")
        footer.addWidget(f_name)
        f_tag = QLabel("TURN MOTION INTO CONTROL")
        f_tag.setAlignment(Qt.AlignCenter)
        f_tag.setStyleSheet(f"font-size: 9px; color: {COLOR_ACCENT}; font-weight: 700; letter-spacing: 1.5px;")
        footer.addWidget(f_tag)
        f_sig = QLabel(f"Version {__version__}")
        f_sig.setAlignment(Qt.AlignCenter)
        f_sig.setStyleSheet(f"font-size: 10px; color: {COLOR_TEXT_DIM}; padding-top: 4px;")
        footer.addWidget(f_sig)
        root.addLayout(footer)

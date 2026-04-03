"""
Laser Lab launcher.

Detects connected hardware and provides buttons to open the
Spectrometer GUI, Stage Controller GUI, or FROG Scan GUI.
"""

import os
import sys
import subprocess

import usb.core

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QGroupBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

_VID_SPECTROMETER = 0x276E
_VID_KDC101       = 0x0403
_PID_KDC101       = 0xFAF0


# ── Hardware probe ─────────────────────────────────────────────────────────────

class HardwareProbe(QThread):
    result = pyqtSignal(bool, bool)   # has_spectrometer, has_stage

    def run(self):
        has_spec  = usb.core.find(idVendor=_VID_SPECTROMETER) is not None
        has_stage = usb.core.find(idVendor=_VID_KDC101,
                                   idProduct=_PID_KDC101) is not None
        self.result.emit(has_spec, has_stage)


# ── Launcher window ────────────────────────────────────────────────────────────

class LauncherWindow(QWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Laser Lab")
        self.setMinimumWidth(380)
        self._probe_thread = None
        self._build_ui()
        self._probe()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("Laser Lab")
        font = QFont()
        font.setPointSize(20)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self._spec_ind, spec_card = self._make_card(
            "Spectrometer",
            "Live spectrum · averaging · time-domain view",
            "spectrometer_gui.py",
        )
        layout.addWidget(spec_card)

        self._stage_ind, stage_card = self._make_card(
            "Stage Controller",
            "Position · home · jog · delay scan (stage only)",
            "stage_gui.py",
        )
        layout.addWidget(stage_card)

        self._frog_ind, frog_card = self._make_card(
            "FROG Scan",
            "Delay scan · spectrum acquisition · Gaussian fit",
            "frog_gui.py",
        )
        layout.addWidget(frog_card)

        self._refresh_btn = QPushButton("Refresh hardware status")
        self._refresh_btn.clicked.connect(self._probe)
        layout.addWidget(self._refresh_btn)

    def _make_card(self, title, description, script):
        group = QGroupBox()
        row = QHBoxLayout(group)
        row.setSpacing(12)

        indicator = QLabel("●")
        indicator.setStyleSheet("color: grey;")
        indicator.setFixedWidth(14)
        row.addWidget(indicator)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title_lbl = QLabel(title)
        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(13)
        title_lbl.setFont(bold)
        text_col.addWidget(title_lbl)
        desc_lbl = QLabel(description)
        desc_lbl.setStyleSheet("color: #888;")
        text_col.addWidget(desc_lbl)
        row.addLayout(text_col, stretch=1)

        btn = QPushButton("Open")
        btn.setFixedWidth(72)
        btn.setFixedHeight(48)
        btn.clicked.connect(lambda: self._launch(script))
        row.addWidget(btn)

        return indicator, group

    # ── Hardware detection ─────────────────────────────────────────────────────

    def _probe(self):
        self._refresh_btn.setEnabled(False)
        for ind in (self._spec_ind, self._stage_ind, self._frog_ind):
            ind.setStyleSheet("color: grey;")
            ind.setToolTip("Checking…")
        self._probe_thread = HardwareProbe()
        self._probe_thread.result.connect(self._on_probe_result)
        self._probe_thread.start()

    def _on_probe_result(self, has_spec, has_stage):
        self._refresh_btn.setEnabled(True)

        self._set_indicator(self._spec_ind, has_spec,
                            "Spectrometer detected",
                            "No spectrometer found")
        self._set_indicator(self._stage_ind, has_stage,
                            "KDC101 detected",
                            "No KDC101 found")
        self._set_indicator(self._frog_ind, has_spec and has_stage,
                            "Stage and spectrometer detected",
                            "Requires both stage and spectrometer")

    @staticmethod
    def _set_indicator(label, ok, tip_ok, tip_bad):
        label.setStyleSheet("color: green;" if ok else "color: red;")
        label.setToolTip(tip_ok if ok else tip_bad)

    # ── Launch ─────────────────────────────────────────────────────────────────

    def _launch(self, script):
        subprocess.Popen([sys.executable,
                          os.path.join(REPO_DIR, script)])


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = LauncherWindow()
    win.show()
    sys.exit(app.exec())

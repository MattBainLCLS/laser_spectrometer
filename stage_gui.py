"""
Stage controller GUI — standalone window.

Left panel: StageControlWidget (position, home, jog, go-to, t0).
Below:       Delay scan (stage only, no spectrometer).
"""

import sys

import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QGridLayout,
    QPushButton, QLabel, QDoubleSpinBox,
    QGroupBox, QStatusBar, QMessageBox,
    QProgressBar,
)
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont

from stage_widget import StageControlWidget, StageWorker, fs_to_mm, mm_to_fs


# ── Worker: stage-only delay scan ─────────────────────────────────────────────

class ScanWorker(QThread):
    step_done = pyqtSignal(int, float, float)   # index, delay_fs, position_mm
    finished  = pyqtSignal()
    error     = pyqtSignal(str)

    def __init__(self, stage, positions_mm, delays_fs):
        super().__init__()
        self._stage        = stage
        self._positions_mm = positions_mm
        self._delays_fs    = delays_fs
        self._stop         = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            for i, (pos, delay) in enumerate(
                    zip(self._positions_mm, self._delays_fs)):
                if self._stop:
                    break
                self._stage.move_to(pos)
                actual = self._stage.get_position()
                self.step_done.emit(i, delay, actual)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ── Main window ────────────────────────────────────────────────────────────────

class StageWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stage Controller")
        self._worker = None

        self._status = QStatusBar()
        self.setStatusBar(self._status)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        self._stage_ctrl = StageControlWidget()
        self._stage_ctrl.status_message.connect(self._status.showMessage)
        self._stage_ctrl.t0_changed.connect(self._update_range_indicator)
        layout.addWidget(self._stage_ctrl)

        layout.addWidget(self._build_scan_group())
        self.setMinimumWidth(420)

    # ── Scan UI ────────────────────────────────────────────────────────────────

    def _build_scan_group(self):
        scan_group = QGroupBox("Delay Scan")
        scan_layout = QGridLayout(scan_group)

        scan_layout.addWidget(QLabel("Start (fs):"), 0, 0)
        self._scan_start = QDoubleSpinBox()
        self._scan_start.setRange(-100_000, 100_000)
        self._scan_start.setValue(-100_000.0)
        self._scan_start.setDecimals(1)
        self._scan_start.setSuffix(" fs")
        _w = int(self._scan_start.sizeHint().width() * 1.5)
        self._scan_start.setValue(-500.0)
        self._scan_start.setFixedWidth(_w)
        scan_layout.addWidget(self._scan_start, 0, 1)

        scan_layout.addWidget(QLabel("Stop (fs):"), 1, 0)
        self._scan_stop = QDoubleSpinBox()
        self._scan_stop.setRange(-100_000, 100_000)
        self._scan_stop.setValue(500.0)
        self._scan_stop.setDecimals(1)
        self._scan_stop.setSuffix(" fs")
        self._scan_stop.setFixedWidth(_w)
        scan_layout.addWidget(self._scan_stop, 1, 1)

        scan_layout.addWidget(QLabel("Step (fs):"), 2, 0)
        self._scan_step = QDoubleSpinBox()
        self._scan_step.setRange(0.1, 10_000)
        self._scan_step.setValue(50.0)
        self._scan_step.setDecimals(1)
        self._scan_step.setSuffix(" fs")
        self._scan_step.setFixedWidth(_w)
        scan_layout.addWidget(self._scan_step, 2, 1)

        self._scan_nsteps = QLabel("")
        scan_layout.addWidget(self._scan_nsteps, 3, 0)
        self._scan_range_indicator = QLabel("●")
        self._scan_range_indicator.setStyleSheet("color: grey;")
        self._scan_range_indicator.setToolTip("Set t0 to check range")
        scan_layout.addWidget(self._scan_range_indicator, 3, 1)

        for sp in (self._scan_start, self._scan_stop, self._scan_step):
            sp.valueChanged.connect(self._update_scan_preview)
        self._update_scan_preview()

        self._scan_btn = QPushButton("Start Scan")
        self._scan_btn.setFixedHeight(40)
        self._scan_btn.clicked.connect(self._do_scan)
        scan_layout.addWidget(self._scan_btn, 4, 0)

        self._scan_abort_btn = QPushButton("Abort")
        self._scan_abort_btn.setFixedHeight(40)
        self._scan_abort_btn.setEnabled(False)
        self._scan_abort_btn.clicked.connect(self._do_abort_scan)
        scan_layout.addWidget(self._scan_abort_btn, 4, 1)

        self._scan_progress = QProgressBar()
        scan_layout.addWidget(self._scan_progress, 5, 0, 1, 2)

        self._scan_status = QLabel("")
        self._scan_status.setAlignment(QLabel().alignment())
        scan_layout.addWidget(self._scan_status, 6, 0, 1, 2)

        return scan_group

    # ── Scan logic ─────────────────────────────────────────────────────────────

    def _scan_delays(self) -> np.ndarray:
        start, stop, step = (self._scan_start.value(),
                              self._scan_stop.value(),
                              self._scan_step.value())
        if step <= 0 or start == stop:
            return np.array([])
        return np.arange(start, stop + step * 0.5 * np.sign(stop - start),
                         step * np.sign(stop - start))

    def _update_scan_preview(self):
        d = self._scan_delays()
        n = len(d)
        self._scan_nsteps.setText(
            f"{n} steps  ({d[0]:.1f} → {d[-1]:.1f} fs)" if n else "0 steps")
        self._update_range_indicator()

    def _update_range_indicator(self):
        stage  = self._stage_ctrl.stage
        t0_mm  = self._stage_ctrl.t0_mm
        if stage is None or t0_mm is None:
            self._scan_range_indicator.setStyleSheet("color: grey;")
            self._scan_range_indicator.setToolTip("Set t0 to check range")
            return
        delays = self._scan_delays()
        if len(delays) == 0:
            self._scan_range_indicator.setStyleSheet("color: grey;")
            self._scan_range_indicator.setToolTip("")
            return
        positions = t0_mm + fs_to_mm(delays)
        out = np.any((positions < stage.min_position) |
                     (positions > stage.max_position))
        if out:
            self._scan_range_indicator.setStyleSheet("color: red;")
            self._scan_range_indicator.setToolTip(
                "Some positions are outside the stage range")
        else:
            self._scan_range_indicator.setStyleSheet("color: green;")
            self._scan_range_indicator.setToolTip(
                "All positions within stage range")

    def _do_scan(self):
        stage = self._stage_ctrl.stage
        t0_mm = self._stage_ctrl.t0_mm
        if stage is None or t0_mm is None:
            QMessageBox.warning(self, "Not ready",
                                "Connect a stage and set t0 before scanning.")
            return

        delays_fs    = self._scan_delays()
        positions_mm = t0_mm + fs_to_mm(delays_fs)
        clipped      = np.clip(positions_mm, stage.min_position, stage.max_position)
        if not np.allclose(positions_mm, clipped):
            QMessageBox.warning(self, "Range Warning",
                "Some positions are outside the stage range and will be clamped.")
        positions_mm = clipped

        self._scan_progress.setMaximum(len(delays_fs))
        self._scan_progress.setValue(0)
        self._scan_status.setText("")
        self._set_scan_controls_enabled(False)
        self._stage_ctrl.set_busy(True)
        self._status.showMessage("Scanning…")

        self._worker = ScanWorker(stage, positions_mm, delays_fs)
        self._worker.step_done.connect(self._on_scan_step)
        self._worker.finished.connect(self._on_scan_done)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _do_abort_scan(self):
        if isinstance(self._worker, ScanWorker):
            self._worker.stop()
            self._scan_status.setText("Aborting…")

    def _on_scan_step(self, index, delay_fs, position_mm):
        self._scan_progress.setValue(index + 1)
        self._scan_status.setText(
            f"Step {index + 1}/{self._scan_progress.maximum()}  "
            f"{delay_fs:+.1f} fs  →  {position_mm:.4f} mm")
        self._stage_ctrl.update_position_display(position_mm)

    def _on_scan_done(self):
        self._worker.wait()
        self._worker = None
        self._stage_ctrl.set_busy(False)
        self._set_scan_controls_enabled(True)
        self._status.showMessage("Scan complete.")
        self._scan_status.setText("Done.")

    def _on_scan_error(self, msg):
        self._worker.wait()
        self._worker = None
        self._stage_ctrl.set_busy(False)
        self._set_scan_controls_enabled(True)
        self._status.showMessage(f"Error: {msg}")
        QMessageBox.warning(self, "Scan Error", msg)

    def _set_scan_controls_enabled(self, enabled: bool):
        self._scan_btn.setEnabled(enabled)
        self._scan_abort_btn.setEnabled(not enabled)

    def closeEvent(self, event):
        if self._worker is not None:
            if isinstance(self._worker, ScanWorker):
                self._worker.stop()
            self._worker.wait()
        self._stage_ctrl.shutdown()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = StageWindow()
    win.show()
    sys.exit(app.exec())

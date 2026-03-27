"""
Thorlabs KDC101 / MTS25-Z8 stage GUI.

Features:
  - Live position readback (polls every 250 ms), shown in mm and fs relative to t0
  - Home button
  - Jog forward / backward with selectable step size
  - Go-to-position (absolute, mm)
  - t0 reference — set current position as time-zero
  - Delay scan — sweep over a range of delays in femtoseconds
"""

import sys
import time

import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QDoubleSpinBox,
    QGroupBox, QStatusBar, QMessageBox,
    QProgressBar,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont

import stage as st

# Speed of light in mm/fs  (c = 299.792458 mm/ns = 2.99792458e-4 mm/fs)
C_MM_PER_FS = 2.99792458e-4

def fs_to_mm(delay_fs: float) -> float:
    """Convert a delay in femtoseconds to a stage displacement in mm.
    Factor of 2: optical path = 2 × physical displacement."""
    return delay_fs * C_MM_PER_FS / 2.0

def mm_to_fs(displacement_mm: float) -> float:
    """Convert a stage displacement in mm to optical delay in femtoseconds."""
    return displacement_mm * 2.0 / C_MM_PER_FS


# ── Worker: single blocking stage command ────────────────────────────────────

class StageWorker(QThread):
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, dev, command, *args):
        super().__init__()
        self._dev     = dev
        self._command = command
        self._args    = args

    def run(self):
        try:
            self._command(self._dev, *self._args)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ── Worker: delay scan ───────────────────────────────────────────────────────

class ScanWorker(QThread):
    step_done = pyqtSignal(int, float, float)   # index, delay_fs, position_mm
    finished  = pyqtSignal()
    error     = pyqtSignal(str)

    def __init__(self, dev, positions_mm, delays_fs):
        super().__init__()
        self._dev          = dev
        self._positions_mm = positions_mm
        self._delays_fs    = delays_fs
        self._stop         = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            for i, (pos, delay) in enumerate(zip(self._positions_mm, self._delays_fs)):
                if self._stop:
                    break
                st.move_to(self._dev, pos)
                actual = st.get_position(self._dev)
                self.step_done.emit(i, delay, actual)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ── Main window ───────────────────────────────────────────────────────────────

class StageWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stage Controller — KDC101 / MTS25-Z8")
        self._dev    = None
        self._worker = None
        self._t0_mm  = None          # t0 reference position in mm

        self._build_ui()
        self._connect_device()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_position)
        self._poll_timer.start(250)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── Position display ──────────────────────────────────────────────────
        pos_group = QGroupBox("Position")
        pos_inner = QVBoxLayout(pos_group)

        self._pos_label = QLabel("—")
        big = QFont(); big.setPointSize(28); big.setBold(True)
        self._pos_label.setFont(big)
        self._pos_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pos_inner.addWidget(self._pos_label)

        self._delay_label = QLabel("")
        med = QFont(); med.setPointSize(13)
        self._delay_label.setFont(med)
        self._delay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._delay_label.setStyleSheet("color: #5588cc;")
        pos_inner.addWidget(self._delay_label)

        layout.addWidget(pos_group)

        # ── Home ──────────────────────────────────────────────────────────────
        home_group = QGroupBox("Home")
        home_layout = QHBoxLayout(home_group)
        self._home_btn = QPushButton("Home Stage")
        self._home_btn.setFixedHeight(40)
        self._home_btn.clicked.connect(self._do_home)
        home_layout.addWidget(self._home_btn)
        layout.addWidget(home_group)

        # ── Jog ───────────────────────────────────────────────────────────────
        jog_group = QGroupBox("Jog")
        jog_layout = QGridLayout(jog_group)

        jog_layout.addWidget(QLabel("Step (mm):"), 0, 0)
        self._step_spin = QDoubleSpinBox()
        self._step_spin.setRange(0.001, 25.0)
        self._step_spin.setValue(1.0)
        self._step_spin.setDecimals(3)
        self._step_spin.setSuffix(" mm")
        jog_layout.addWidget(self._step_spin, 0, 1, 1, 2)

        self._jog_back_btn = QPushButton("◀  Jog –")
        self._jog_back_btn.setFixedHeight(40)
        self._jog_back_btn.clicked.connect(self._do_jog_back)
        jog_layout.addWidget(self._jog_back_btn, 1, 0)

        self._jog_fwd_btn = QPushButton("Jog +  ▶")
        self._jog_fwd_btn.setFixedHeight(40)
        self._jog_fwd_btn.clicked.connect(self._do_jog_fwd)
        jog_layout.addWidget(self._jog_fwd_btn, 1, 1, 1, 2)

        layout.addWidget(jog_group)

        # ── Go to position ────────────────────────────────────────────────────
        goto_group = QGroupBox("Go To Position")
        goto_layout = QHBoxLayout(goto_group)

        goto_layout.addWidget(QLabel("Position (mm):"))
        self._goto_spin = QDoubleSpinBox()
        self._goto_spin.setRange(0.0, 25.0)
        self._goto_spin.setValue(0.0)
        self._goto_spin.setDecimals(3)
        self._goto_spin.setSuffix(" mm")
        goto_layout.addWidget(self._goto_spin)

        self._goto_btn = QPushButton("Go")
        self._goto_btn.setFixedHeight(40)
        self._goto_btn.setFixedWidth(80)
        self._goto_btn.clicked.connect(self._do_goto)
        goto_layout.addWidget(self._goto_btn)

        layout.addWidget(goto_group)

        # ── t0 reference ──────────────────────────────────────────────────────
        t0_group = QGroupBox("Time Zero (t0)")
        t0_layout = QGridLayout(t0_group)

        self._t0_display = QLabel("not set")
        self._t0_display.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        t0_layout.addWidget(QLabel("t0:"), 0, 0)
        t0_layout.addWidget(self._t0_display, 0, 1)

        self._set_t0_btn = QPushButton("Set t0 = current position")
        self._set_t0_btn.setFixedHeight(36)
        self._set_t0_btn.clicked.connect(self._do_set_t0)
        t0_layout.addWidget(self._set_t0_btn, 1, 0, 1, 2)

        layout.addWidget(t0_group)

        # ── Delay scan ────────────────────────────────────────────────────────
        scan_group = QGroupBox("Delay Scan")
        scan_layout = QGridLayout(scan_group)

        scan_layout.addWidget(QLabel("Start (fs):"), 0, 0)
        self._scan_start = QDoubleSpinBox()
        self._scan_start.setRange(-100_000, 100_000)
        self._scan_start.setValue(-500.0)
        self._scan_start.setDecimals(1)
        self._scan_start.setSuffix(" fs")
        scan_layout.addWidget(self._scan_start, 0, 1)

        scan_layout.addWidget(QLabel("Stop (fs):"), 1, 0)
        self._scan_stop = QDoubleSpinBox()
        self._scan_stop.setRange(-100_000, 100_000)
        self._scan_stop.setValue(500.0)
        self._scan_stop.setDecimals(1)
        self._scan_stop.setSuffix(" fs")
        scan_layout.addWidget(self._scan_stop, 1, 1)

        scan_layout.addWidget(QLabel("Step (fs):"), 2, 0)
        self._scan_step = QDoubleSpinBox()
        self._scan_step.setRange(0.1, 10_000)
        self._scan_step.setValue(50.0)
        self._scan_step.setDecimals(1)
        self._scan_step.setSuffix(" fs")
        scan_layout.addWidget(self._scan_step, 2, 1)

        self._scan_nsteps = QLabel("")
        scan_layout.addWidget(self._scan_nsteps, 3, 0, 1, 2)
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
        self._scan_progress.setValue(0)
        scan_layout.addWidget(self._scan_progress, 5, 0, 1, 2)

        self._scan_status = QLabel("")
        self._scan_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scan_layout.addWidget(self._scan_status, 6, 0, 1, 2)

        layout.addWidget(scan_group)

        self._status = QStatusBar()
        self.setStatusBar(self._status)

        self.setMinimumWidth(420)

    # ── Device connection ─────────────────────────────────────────────────────

    def _connect_device(self):
        try:
            self._dev = st._open_ftdi()
            self._status.showMessage("Connected to KDC101.")
        except Exception as e:
            self._set_busy(True)
            QMessageBox.critical(self, "Connection Error",
                                 f"Could not connect to KDC101:\n{e}")

    # ── Position polling ──────────────────────────────────────────────────────

    def _poll_position(self):
        if self._dev is None or self._worker is not None:
            return
        try:
            pos = st.get_position(self._dev)
            self._pos_label.setText(f"{pos:.4f} mm")
            if self._t0_mm is not None:
                delay = mm_to_fs(pos - self._t0_mm)
                self._delay_label.setText(f"{delay:+.1f} fs  (t0 = {self._t0_mm:.4f} mm)")
            else:
                self._delay_label.setText("")
        except Exception:
            pass

    # ── t0 ────────────────────────────────────────────────────────────────────

    def _do_set_t0(self):
        if self._dev is None:
            return
        try:
            self._t0_mm = st.get_position(self._dev)
            self._t0_display.setText(f"{self._t0_mm:.4f} mm")
            self._status.showMessage(f"t0 set to {self._t0_mm:.4f} mm.")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    # ── Scan preview ──────────────────────────────────────────────────────────

    def _update_scan_preview(self):
        delays = self._scan_delays()
        n = len(delays)
        self._scan_nsteps.setText(
            f"{n} steps  ({delays[0]:.1f} → {delays[-1]:.1f} fs)" if n else "0 steps"
        )

    def _scan_delays(self) -> np.ndarray:
        start = self._scan_start.value()
        stop  = self._scan_stop.value()
        step  = self._scan_step.value()
        if step <= 0 or start == stop:
            return np.array([])
        return np.arange(start, stop + step * 0.5 * np.sign(stop - start),
                         step * np.sign(stop - start))

    # ── Scan ─────────────────────────────────────────────────────────────────

    def _do_scan(self):
        if self._dev is None:
            return
        if self._t0_mm is None:
            QMessageBox.warning(self, "No t0", "Set t0 before scanning.")
            return

        delays_fs    = self._scan_delays()
        positions_mm = self._t0_mm + fs_to_mm(delays_fs)

        # Clamp and warn if any positions are out of range
        clipped = np.clip(positions_mm, 0.0, 25.0)
        if not np.allclose(positions_mm, clipped):
            QMessageBox.warning(self, "Range Warning",
                "Some scan positions fall outside the stage range (0–25 mm) "
                "and will be clamped.")
        positions_mm = clipped

        self._scan_progress.setMaximum(len(delays_fs))
        self._scan_progress.setValue(0)
        self._scan_status.setText("")

        self._set_controls_enabled(False, scanning=True)

        self._worker = ScanWorker(self._dev, positions_mm, delays_fs)
        self._worker.step_done.connect(self._on_scan_step)
        self._worker.finished.connect(self._on_scan_done)
        self._worker.error.connect(self._on_command_error)
        self._worker.start()

    def _do_abort_scan(self):
        if isinstance(self._worker, ScanWorker):
            self._worker.stop()
            self._scan_status.setText("Aborting…")

    def _on_scan_step(self, index: int, delay_fs: float, position_mm: float):
        self._scan_progress.setValue(index + 1)
        self._scan_status.setText(
            f"Step {index + 1}/{self._scan_progress.maximum()}  "
            f"delay = {delay_fs:+.1f} fs  pos = {position_mm:.4f} mm"
        )
        self._pos_label.setText(f"{position_mm:.4f} mm")

    def _on_scan_done(self):
        self._cleanup_worker()
        self._set_controls_enabled(True)
        self._status.showMessage("Scan complete.")
        self._scan_status.setText("Done.")

    # ── Generic single-command helpers ────────────────────────────────────────

    def _do_home(self):
        self._run_command(st.home)

    def _do_jog_fwd(self):
        try:
            current = st.get_position(self._dev)
        except Exception:
            return
        self._run_command(st.move_to, min(current + self._step_spin.value(), 25.0))

    def _do_jog_back(self):
        try:
            current = st.get_position(self._dev)
        except Exception:
            return
        self._run_command(st.move_to, max(current - self._step_spin.value(), 0.0))

    def _do_goto(self):
        self._run_command(st.move_to, self._goto_spin.value())

    def _run_command(self, command, *args):
        if self._dev is None:
            return
        self._set_controls_enabled(False, scanning=False)
        self._worker = StageWorker(self._dev, command, *args)
        self._worker.finished.connect(self._on_command_done)
        self._worker.error.connect(self._on_command_error)
        self._worker.start()

    def _on_command_done(self):
        self._cleanup_worker()
        self._set_controls_enabled(True)
        self._status.showMessage("Ready.")

    def _on_command_error(self, msg):
        self._cleanup_worker()
        self._set_controls_enabled(True)
        self._status.showMessage(f"Error: {msg}")
        QMessageBox.warning(self, "Stage Error", msg)

    def _cleanup_worker(self):
        """Wait for the worker thread to fully exit before clearing the reference."""
        if self._worker is not None:
            self._worker.wait()
            self._worker = None

    def _set_controls_enabled(self, enabled: bool, scanning: bool = False):
        """Enable or disable all interactive controls.

        When disabling (enabled=False), scanning=True shows the Abort button
        instead of leaving Scan active; scanning=False is for single moves.
        """
        for w in (self._home_btn, self._jog_back_btn, self._jog_fwd_btn,
                  self._goto_btn, self._set_t0_btn, self._scan_btn):
            w.setEnabled(enabled)
        self._scan_abort_btn.setEnabled(not enabled and scanning)
        if not enabled:
            self._status.showMessage("Moving…")

    def closeEvent(self, event):
        self._poll_timer.stop()
        if self._worker is not None:
            if isinstance(self._worker, ScanWorker):
                self._worker.stop()
            self._worker.wait()
        event.accept()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = StageWindow()
    win.show()
    sys.exit(app.exec())

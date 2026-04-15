"""
Reusable stage-control panel widget.

Provides position display, home, jog, go-to and t0 controls for any
StageBase implementation. Can be embedded in a larger window or run
standalone via StageWindow in stage_gui.py.

Public API
----------
stage           — connected StageBase instance, or None
t0_mm           — current t0 reference position, or None
t0_changed      — signal(float): emitted when t0 is set
stage_connected — signal(): emitted after successful connection
status_message  — signal(str): status-bar text for the host window
set_busy(bool)  — disable controls and pause polling during external ops
update_position_display(float) — update position/delay labels from outside
shutdown()      — stop polling, wait for workers, close stage
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import (
    QApplication, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QDoubleSpinBox,
    QGroupBox, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont

from hardware.stages import find_stage, StageBase

C_MM_PER_FS = 2.99792458e-4   # mm per femtosecond


def fs_to_mm(delay_fs: float) -> float:
    return delay_fs * C_MM_PER_FS / 2.0


def mm_to_fs(displacement_mm: float) -> float:
    return displacement_mm * 2.0 / C_MM_PER_FS


# ── Worker ─────────────────────────────────────────────────────────────────────

class StageWorker(QThread):
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, command, *args):
        super().__init__()
        self._command, self._args = command, args

    def run(self):
        try:
            self._command(*self._args)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ── Widget ─────────────────────────────────────────────────────────────────────

class StageControlWidget(QWidget):

    t0_changed      = pyqtSignal(float)  # emitted when t0 is set; arg = t0_mm
    stage_connected = pyqtSignal()       # emitted after successful connection
    status_message  = pyqtSignal(str)    # status-bar text for the host window

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stage  = None
        self._worker = None
        self._t0_mm  = None
        self._busy   = False   # True while an external operation holds the stage

        self._build_ui()
        self._connect_stage()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(250)

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def stage(self) -> StageBase | None:
        return self._stage

    @property
    def t0_mm(self) -> float | None:
        return self._t0_mm

    def set_busy(self, busy: bool):
        """Disable/enable controls — call when an external operation is active."""
        self._busy = busy
        self._set_controls_enabled(not busy)

    def update_position_display(self, pos_mm: float):
        """Update position and delay labels — call from an external scan step."""
        self._pos_label.setText(f"{pos_mm:.4f} mm")
        if self._t0_mm is not None:
            delay = mm_to_fs(pos_mm - self._t0_mm)
            self._delay_label.setText(
                f"{delay:+.1f} fs  (t0 = {self._t0_mm:.4f} mm)")
        else:
            self._delay_label.setText("")

    def shutdown(self):
        """Stop polling, wait for any running worker, close the stage."""
        self._poll_timer.stop()
        if self._worker is not None:
            self._worker.wait()
        if self._stage is not None:
            try:
                self._stage.close()
            except Exception:
                pass

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # Position display
        pos_group = QGroupBox("Position")
        pos_inner = QVBoxLayout(pos_group)
        self._pos_label = QLabel("—")
        big = QFont(); big.setPointSize(24); big.setBold(True)
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

        # Home
        home_group = QGroupBox("Home")
        home_layout = QHBoxLayout(home_group)
        self._home_btn = QPushButton("Home Stage")
        self._home_btn.setFixedHeight(40)
        self._home_btn.clicked.connect(self._do_home)
        home_layout.addWidget(self._home_btn)
        home_layout.addStretch()
        homed_widget = QWidget()
        homed_inner = QHBoxLayout(homed_widget)
        homed_inner.setContentsMargins(0, 0, 0, 0)
        homed_inner.setSpacing(4)
        homed_inner.addWidget(QLabel("Homed?"))
        self._home_indicator = QLabel("●")
        self._home_indicator.setStyleSheet("color: red;")
        self._home_indicator.setToolTip("Not homed")
        homed_inner.addWidget(self._home_indicator)
        home_layout.addWidget(homed_widget)
        layout.addWidget(home_group)

        # Jog
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

        # Go to position
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

        # t0
        t0_group = QGroupBox("Time Zero (t0)")
        t0_layout = QGridLayout(t0_group)
        t0_layout.addWidget(QLabel("t0:"), 0, 0)
        self._t0_display = QLabel("not set")
        self._t0_display.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        t0_layout.addWidget(self._t0_display, 0, 1)
        self._set_t0_btn = QPushButton("Set t0 = current position")
        self._set_t0_btn.setFixedHeight(36)
        self._set_t0_btn.clicked.connect(self._do_set_t0)
        t0_layout.addWidget(self._set_t0_btn, 1, 0, 1, 2)
        layout.addWidget(t0_group)

    # ── Stage connection ────────────────────────────────────────────────────────

    def _connect_stage(self):
        stage = find_stage()
        if stage is None:
            self._set_controls_enabled(False)
            QMessageBox.critical(self, "Connection Error",
                                 "No stage found — is it plugged in and powered on?")
            return
        self._stage = stage
        self._goto_spin.setRange(stage.min_position, stage.max_position)
        self.status_message.emit(f"Connected: {stage.model_name}")
        self.stage_connected.emit()

    # ── Polling ─────────────────────────────────────────────────────────────────

    def _poll(self):
        if self._stage is None or self._worker is not None or self._busy:
            return
        try:
            self.update_position_display(self._stage.get_position())
        except Exception:
            pass
        try:
            homed = self._stage.is_homed()
            self._home_indicator.setStyleSheet(
                "color: green;" if homed else "color: red;")
            self._home_indicator.setToolTip("Homed" if homed else "Not homed")
        except Exception:
            pass

    # ── t0 ──────────────────────────────────────────────────────────────────────

    def _do_set_t0(self):
        if self._stage is None:
            return
        try:
            self._t0_mm = self._stage.get_position()
            self._t0_display.setText(f"{self._t0_mm:.4f} mm")
            self.status_message.emit(f"t0 set to {self._t0_mm:.4f} mm.")
            self.t0_changed.emit(self._t0_mm)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    # ── Commands ────────────────────────────────────────────────────────────────

    def _do_home(self):
        self._run_command(self._stage.home)

    def _do_jog_fwd(self):
        try:
            cur = self._stage.get_position()
        except Exception:
            return
        self._run_command(self._stage.move_to,
                          min(cur + self._step_spin.value(),
                              self._stage.max_position))

    def _do_jog_back(self):
        try:
            cur = self._stage.get_position()
        except Exception:
            return
        self._run_command(self._stage.move_to,
                          max(cur - self._step_spin.value(),
                              self._stage.min_position))

    def _do_goto(self):
        self._run_command(self._stage.move_to, self._goto_spin.value())

    def _run_command(self, command, *args):
        if self._stage is None:
            return
        self._set_controls_enabled(False)
        self._worker = StageWorker(command, *args)
        self._worker.finished.connect(self._on_command_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_command_done(self):
        self._cleanup_worker()
        self._set_controls_enabled(True)
        self.status_message.emit("Ready.")

    def _on_error(self, msg):
        self._cleanup_worker()
        self._set_controls_enabled(True)
        self.status_message.emit(f"Error: {msg}")
        QMessageBox.warning(self, "Stage Error", msg)

    def _cleanup_worker(self):
        if self._worker is not None:
            self._worker.wait()
            self._worker = None

    def _set_controls_enabled(self, enabled: bool):
        for w in (self._home_btn, self._jog_back_btn, self._jog_fwd_btn,
                  self._goto_btn, self._set_t0_btn):
            w.setEnabled(enabled)
        if not enabled and not self._busy:
            self.status_message.emit("Moving…")

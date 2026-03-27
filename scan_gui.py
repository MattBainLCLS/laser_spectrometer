"""
Combined delay-stage + spectrometer scan GUI.

Left panel  — stage controls (position, home, jog, go-to, t0, scan params)
Right panel — live 2D plot of acquired spectra vs delay, save button

Each scan step:
  1. Move stage to target position
  2. Acquire one spectrum from the spectrometer
  3. Update the 2D colormap live
"""

import sys
import time

import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter,
    QPushButton, QLabel, QDoubleSpinBox, QSpinBox,
    QGroupBox, QStatusBar, QMessageBox, QProgressBar,
    QFileDialog,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from stages import StageBase, find_stage
from spectrometers import find_spectrometer, SpectrometerBase

C_MM_PER_FS = 2.99792458e-4   # mm per femtosecond

def fs_to_mm(delay_fs):
    return delay_fs * C_MM_PER_FS / 2.0

def mm_to_fs(displacement_mm):
    return displacement_mm * 2.0 / C_MM_PER_FS


# ── Worker: single blocking stage command ─────────────────────────────────────

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


# ── Worker: delay scan with spectrum acquisition ──────────────────────────────

class ScanWorker(QThread):
    # index, delay_fs, position_mm, wavelengths (1-D), spectrum (1-D)
    step_done = pyqtSignal(int, float, float, object, object)
    finished  = pyqtSignal()
    error     = pyqtSignal(str)

    def __init__(self, stage: StageBase, spec: SpectrometerBase,
                 positions_mm, delays_fs):
        super().__init__()
        self._stage        = stage
        self._spec         = spec
        self._positions_mm = positions_mm
        self._delays_fs    = delays_fs
        self._stop         = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            wavelengths = np.asarray(self._spec.get_wavelengths())
            for i, (pos, delay) in enumerate(
                    zip(self._positions_mm, self._delays_fs)):
                if self._stop:
                    break
                self._stage.move_to(pos)
                actual = self._stage.get_position()

                # Acquire spectrum
                self._spec.start_exposure()
                deadline = time.monotonic() + self._spec.exposure_time + 2.0
                while not self._spec.is_data_ready():
                    if time.monotonic() > deadline:
                        raise TimeoutError("Spectrometer timed out")
                    time.sleep(0.005)
                result = self._spec.get_spectrum()

                self.step_done.emit(i, delay, actual,
                                    wavelengths, result.spectrum)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ── 2-D scan plot ─────────────────────────────────────────────────────────────

class ScanPlot(QWidget):
    def __init__(self):
        super().__init__()
        self._fig  = Figure(figsize=(6, 5), tight_layout=True)
        self._ax   = self._fig.add_subplot(111)
        self._canvas = FigureCanvasQTAgg(self._fig)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas)

        self._mesh  = None
        self._data  = None   # (n_delays, n_wl) accumulation array
        self._delays    = None
        self._wavelengths = None

    def init_scan(self, delays_fs: np.ndarray, wavelengths: np.ndarray):
        self._delays      = delays_fs
        self._wavelengths = wavelengths
        self._data = np.full((len(delays_fs), len(wavelengths)), np.nan)

        self._ax.cla()
        # pcolormesh needs bin edges; approximate with midpoints extended
        def edges(arr):
            d = np.diff(arr)
            return np.concatenate([[arr[0] - d[0]/2],
                                    arr[:-1] + d/2,
                                    [arr[-1] + d[-1]/2]])

        wl_e = edges(wavelengths)
        dl_e = edges(delays_fs)
        self._mesh = self._ax.pcolormesh(
            wl_e, dl_e, self._data,
            cmap="inferno", shading="flat"
        )
        self._fig.colorbar(self._mesh, ax=self._ax, label="Intensity")
        self._ax.set_xlabel("Wavelength (nm)")
        self._ax.set_ylabel("Delay (fs)")
        self._ax.set_title("Scan")
        self._canvas.draw()

    def update_step(self, index: int, spectrum: np.ndarray):
        if self._data is None:
            return
        self._data[index] = spectrum
        valid = self._data[~np.all(np.isnan(self._data), axis=1)]
        if valid.size:
            vmin, vmax = np.nanmin(valid), np.nanmax(valid)
            self._mesh.set_array(self._data.ravel())
            self._mesh.set_clim(vmin, vmax)
        self._canvas.draw_idle()

    def get_data(self):
        return self._delays, self._wavelengths, self._data


# ── Main window ───────────────────────────────────────────────────────────────

class ScanWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Delay Scan")
        self._stage  = None
        self._spec   = None
        self._worker = None
        self._t0_mm  = None

        self._build_ui()
        self._connect_stage()


        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_position)
        self._poll_timer.start(250)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        # ── Left: controls ────────────────────────────────────────────────────
        left = QWidget()
        left.setMinimumWidth(360)
        left.setMaximumWidth(420)
        ctrl = QVBoxLayout(left)
        ctrl.setSpacing(10)
        ctrl.setContentsMargins(12, 12, 12, 12)

        # Position
        pos_group = QGroupBox("Stage Position")
        pos_inner = QVBoxLayout(pos_group)
        self._pos_label = QLabel("—")
        font = QFont(); font.setPointSize(24); font.setBold(True)
        self._pos_label.setFont(font)
        self._pos_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pos_inner.addWidget(self._pos_label)
        self._delay_label = QLabel("")
        med = QFont(); med.setPointSize(12)
        self._delay_label.setFont(med)
        self._delay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._delay_label.setStyleSheet("color: #5588cc;")
        pos_inner.addWidget(self._delay_label)
        ctrl.addWidget(pos_group)

        # Home
        home_group = QGroupBox("Home")
        hl = QHBoxLayout(home_group)
        self._home_btn = QPushButton("Home Stage")
        self._home_btn.setFixedHeight(36)
        self._home_btn.clicked.connect(self._do_home)
        hl.addWidget(self._home_btn)
        ctrl.addWidget(home_group)

        # Jog
        jog_group = QGroupBox("Jog")
        jl = QGridLayout(jog_group)
        jl.addWidget(QLabel("Step (mm):"), 0, 0)
        self._step_spin = QDoubleSpinBox()
        self._step_spin.setRange(0.001, 25.0); self._step_spin.setValue(1.0)
        self._step_spin.setDecimals(3); self._step_spin.setSuffix(" mm")
        jl.addWidget(self._step_spin, 0, 1, 1, 2)
        self._jog_back_btn = QPushButton("◀  Jog –")
        self._jog_back_btn.setFixedHeight(36)
        self._jog_back_btn.clicked.connect(self._do_jog_back)
        jl.addWidget(self._jog_back_btn, 1, 0)
        self._jog_fwd_btn = QPushButton("Jog +  ▶")
        self._jog_fwd_btn.setFixedHeight(36)
        self._jog_fwd_btn.clicked.connect(self._do_jog_fwd)
        jl.addWidget(self._jog_fwd_btn, 1, 1, 1, 2)
        ctrl.addWidget(jog_group)

        # Go to
        goto_group = QGroupBox("Go To Position")
        gl = QHBoxLayout(goto_group)
        gl.addWidget(QLabel("(mm):"))
        self._goto_spin = QDoubleSpinBox()
        self._goto_spin.setRange(0.0, 25.0); self._goto_spin.setDecimals(3)
        self._goto_spin.setSuffix(" mm")
        gl.addWidget(self._goto_spin)
        self._goto_btn = QPushButton("Go")
        self._goto_btn.setFixedHeight(36); self._goto_btn.setFixedWidth(60)
        self._goto_btn.clicked.connect(self._do_goto)
        gl.addWidget(self._goto_btn)
        ctrl.addWidget(goto_group)

        # t0
        t0_group = QGroupBox("Time Zero (t0)")
        tl = QGridLayout(t0_group)
        tl.addWidget(QLabel("t0:"), 0, 0)
        self._t0_display = QLabel("not set")
        self._t0_display.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tl.addWidget(self._t0_display, 0, 1)
        self._set_t0_btn = QPushButton("Set t0 = current position")
        self._set_t0_btn.setFixedHeight(34)
        self._set_t0_btn.clicked.connect(self._do_set_t0)
        tl.addWidget(self._set_t0_btn, 1, 0, 1, 2)
        ctrl.addWidget(t0_group)

        # Spectrometer
        spec_group = QGroupBox("Spectrometer")
        sl = QGridLayout(spec_group)
        self._spec_status = QLabel("Not connected")
        self._spec_status.setStyleSheet("color: grey;")
        sl.addWidget(self._spec_status, 0, 0, 1, 2)
        self._spec_connect_btn = QPushButton("Connect Spectrometer")
        self._spec_connect_btn.setFixedHeight(34)
        self._spec_connect_btn.clicked.connect(self._connect_spectrometer)
        sl.addWidget(self._spec_connect_btn, 1, 0, 1, 2)
        sl.addWidget(QLabel("Exposure (ms):"), 2, 0)
        self._exposure_spin = QDoubleSpinBox()
        self._exposure_spin.setRange(1, 60000); self._exposure_spin.setValue(100)
        self._exposure_spin.setDecimals(1); self._exposure_spin.setSuffix(" ms")
        self._exposure_spin.valueChanged.connect(self._update_exposure)
        sl.addWidget(self._exposure_spin, 2, 1)
        ctrl.addWidget(spec_group)

        # Scan
        scan_group = QGroupBox("Delay Scan")
        scl = QGridLayout(scan_group)
        scl.addWidget(QLabel("Start (fs):"), 0, 0)
        self._scan_start = QDoubleSpinBox()
        self._scan_start.setRange(-100_000, 100_000); self._scan_start.setValue(-500)
        self._scan_start.setDecimals(1); self._scan_start.setSuffix(" fs")
        scl.addWidget(self._scan_start, 0, 1)
        scl.addWidget(QLabel("Stop (fs):"), 1, 0)
        self._scan_stop = QDoubleSpinBox()
        self._scan_stop.setRange(-100_000, 100_000); self._scan_stop.setValue(500)
        self._scan_stop.setDecimals(1); self._scan_stop.setSuffix(" fs")
        scl.addWidget(self._scan_stop, 1, 1)
        scl.addWidget(QLabel("Step (fs):"), 2, 0)
        self._scan_step = QDoubleSpinBox()
        self._scan_step.setRange(0.1, 10_000); self._scan_step.setValue(50)
        self._scan_step.setDecimals(1); self._scan_step.setSuffix(" fs")
        scl.addWidget(self._scan_step, 2, 1)
        self._scan_preview = QLabel("")
        scl.addWidget(self._scan_preview, 3, 0, 1, 2)
        for sp in (self._scan_start, self._scan_stop, self._scan_step):
            sp.valueChanged.connect(self._update_scan_preview)
        self._update_scan_preview()

        btn_row = QHBoxLayout()
        self._scan_btn = QPushButton("Start Scan")
        self._scan_btn.setFixedHeight(38)
        self._scan_btn.clicked.connect(self._do_scan)
        btn_row.addWidget(self._scan_btn)
        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setFixedHeight(38)
        self._abort_btn.setEnabled(False)
        self._abort_btn.clicked.connect(self._do_abort)
        btn_row.addWidget(self._abort_btn)
        scl.addLayout(btn_row, 4, 0, 1, 2)

        self._progress = QProgressBar()
        scl.addWidget(self._progress, 5, 0, 1, 2)
        self._scan_step_label = QLabel("")
        self._scan_step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scl.addWidget(self._scan_step_label, 6, 0, 1, 2)

        self._save_btn = QPushButton("Save Scan Data…")
        self._save_btn.setFixedHeight(34)
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._do_save)
        scl.addWidget(self._save_btn, 7, 0, 1, 2)

        ctrl.addWidget(scan_group)
        ctrl.addStretch()

        # ── Right: plot ───────────────────────────────────────────────────────
        self._plot = ScanPlot()

        splitter.addWidget(left)
        splitter.addWidget(self._plot)
        splitter.setStretchFactor(1, 1)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self.resize(1100, 650)

    # ── Stage connection ──────────────────────────────────────────────────────

    def _connect_stage(self):
        stage = find_stage()
        if stage is None:
            self._set_controls_enabled(False)
            QMessageBox.critical(self, "Stage Error",
                                 "No stage found — is it plugged in and powered on?")
            return
        self._stage = stage
        self._goto_spin.setRange(stage.min_position, stage.max_position)
        self._status.showMessage(f"Stage connected: {stage.model_name}")

    # ── Spectrometer connection ───────────────────────────────────────────────

    def _connect_spectrometer(self):
        try:
            spec = find_spectrometer()
            if spec is None:
                QMessageBox.warning(self, "Not Found",
                                    "No spectrometer detected.")
                return
            spec.open()
            spec.exposure_time = self._exposure_spin.value() / 1000.0
            self._spec = spec
            self._spec_status.setText(
                f"{spec.model_name}  s/n {spec.serial_number}")
            self._spec_status.setStyleSheet("color: green;")
            self._spec_connect_btn.setEnabled(False)
            self._status.showMessage("Spectrometer connected.")
        except Exception as e:
            QMessageBox.critical(self, "Spectrometer Error", str(e))

    def _update_exposure(self, value_ms):
        if self._spec is not None:
            self._spec.exposure_time = value_ms / 1000.0

    # ── Position polling ──────────────────────────────────────────────────────

    def _poll_position(self):
        if self._stage is None or self._worker is not None:
            return
        try:
            pos = self._stage.get_position()
            self._pos_label.setText(f"{pos:.4f} mm")
            if self._t0_mm is not None:
                delay = mm_to_fs(pos - self._t0_mm)
                self._delay_label.setText(
                    f"{delay:+.1f} fs  (t0 = {self._t0_mm:.4f} mm)")
            else:
                self._delay_label.setText("")
        except Exception:
            pass

    # ── t0 ────────────────────────────────────────────────────────────────────

    def _do_set_t0(self):
        if self._stage is None:
            return
        try:
            self._t0_mm = self._stage.get_position()
            self._t0_display.setText(f"{self._t0_mm:.4f} mm")
            self._status.showMessage(f"t0 set to {self._t0_mm:.4f} mm.")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    # ── Scan preview ──────────────────────────────────────────────────────────

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
        self._scan_preview.setText(
            f"{n} steps  ({d[0]:.1f} → {d[-1]:.1f} fs)" if n else "0 steps")

    # ── Scan ─────────────────────────────────────────────────────────────────

    def _do_scan(self):
        if self._stage is None or self._t0_mm is None:
            QMessageBox.warning(self, "Not ready",
                                "Connect a stage and set t0 before scanning.")
            return
        if self._spec is None:
            QMessageBox.warning(self, "No Spectrometer",
                                "Connect a spectrometer before scanning.")
            return

        delays_fs    = self._scan_delays()
        positions_mm = np.clip(self._t0_mm + fs_to_mm(delays_fs),
                               self._stage.min_position,
                               self._stage.max_position)

        if not np.allclose(self._t0_mm + fs_to_mm(delays_fs), positions_mm):
            QMessageBox.warning(self, "Range Warning",
                "Some positions are outside the stage range and will be clamped.")

        try:
            wl = np.asarray(self._spec.get_wavelengths())
        except Exception as e:
            QMessageBox.critical(self, "Spectrometer Error", str(e))
            return

        self._plot.init_scan(delays_fs, wl)
        self._progress.setMaximum(len(delays_fs))
        self._progress.setValue(0)
        self._scan_step_label.setText("")
        self._save_btn.setEnabled(False)

        self._set_controls_enabled(False, scanning=True)

        self._worker = ScanWorker(self._stage, self._spec, positions_mm, delays_fs)
        self._worker.step_done.connect(self._on_scan_step)
        self._worker.finished.connect(self._on_scan_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _do_abort(self):
        if isinstance(self._worker, ScanWorker):
            self._worker.stop()
            self._scan_step_label.setText("Aborting…")

    def _on_scan_step(self, index, delay_fs, position_mm, wavelengths, spectrum):
        self._progress.setValue(index + 1)
        self._scan_step_label.setText(
            f"Step {index + 1}/{self._progress.maximum()}  "
            f"{delay_fs:+.1f} fs  →  {position_mm:.4f} mm")
        self._pos_label.setText(f"{position_mm:.4f} mm")
        self._plot.update_step(index, spectrum)

    def _on_scan_done(self):
        self._cleanup_worker()
        self._set_controls_enabled(True)
        self._save_btn.setEnabled(True)
        self._status.showMessage("Scan complete.")
        self._scan_step_label.setText("Done.")

    # ── Save ─────────────────────────────────────────────────────────────────

    def _do_save(self):
        delays, wavelengths, spectra = self._plot.get_data()
        if spectra is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Scan Data", "scan.npz",
            "NumPy archive (*.npz);;All files (*)")
        if not path:
            return
        np.savez(path,
                 delays_fs=delays,
                 wavelengths_nm=wavelengths,
                 spectra=spectra,
                 t0_mm=np.array([self._t0_mm or 0.0]))
        self._status.showMessage(f"Saved to {path}")

    # ── Single-command helpers ────────────────────────────────────────────────

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
        self._status.showMessage("Ready.")

    def _on_error(self, msg):
        self._cleanup_worker()
        self._set_controls_enabled(True)
        self._status.showMessage(f"Error: {msg}")
        QMessageBox.warning(self, "Error", msg)

    def _cleanup_worker(self):
        if self._worker is not None:
            self._worker.wait()
            self._worker = None

    def _set_controls_enabled(self, enabled: bool, scanning: bool = False):
        for w in (self._home_btn, self._jog_back_btn, self._jog_fwd_btn,
                  self._goto_btn, self._set_t0_btn, self._scan_btn,
                  self._spec_connect_btn if self._spec is None else None):
            if w is not None:
                w.setEnabled(enabled)
        self._abort_btn.setEnabled(not enabled and scanning)
        if not enabled:
            self._status.showMessage("Moving…")

    def closeEvent(self, event):
        self._poll_timer.stop()
        if self._worker is not None:
            if isinstance(self._worker, ScanWorker):
                self._worker.stop()
            self._worker.wait()
        for device in (self._spec, self._stage):
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass
        event.accept()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = ScanWindow()
    win.show()
    sys.exit(app.exec())

"""
Combined delay-stage + spectrometer scan GUI.

Left panel  — StageControlWidget (position, home, jog, go-to, t0)
              + spectrometer connection + scan parameters
Right panel — live 2D plot of acquired spectra vs delay, save button

Each scan step:
  1. Move stage to target position
  2. Acquire one spectrum from the spectrometer
  3. Update the 2D colormap live
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time

import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter,
    QPushButton, QLabel, QDoubleSpinBox, QGroupBox,
    QStatusBar, QMessageBox, QProgressBar,
    QFileDialog,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from hardware.spectrometers import RollingBuffer
from ui.stage_widget import StageControlWidget, fs_to_mm, mm_to_fs
from ui.spectrometer_widget import SpectrometerWidget
from ui.scan_analysis_window import ScanAnalysisWindow


# ── Worker: delay scan with spectrum acquisition ──────────────────────────────

class ScanWorker(QThread):
    # index, delay_fs, position_mm, wavelengths (1-D), spectrum (1-D), std (1-D)
    step_done = pyqtSignal(int, float, float, object, object, object)
    finished  = pyqtSignal()
    error     = pyqtSignal(str)

    def __init__(self, stage, spec, positions_mm, delays_fs,
                 n_averages=1, wl_min=0.0, wl_max=np.inf):
        super().__init__()
        self._stage        = stage
        self._spec         = spec
        self._positions_mm = positions_mm
        self._delays_fs    = delays_fs
        self._n_averages   = n_averages
        self._wl_min       = wl_min
        self._wl_max       = wl_max
        self._stop         = False

    def stop(self):
        self._stop = True

    def run(self):
        buffer  = RollingBuffer(self._spec, self._n_averages)
        stop_fn = lambda: self._stop
        try:
            wavelengths = np.asarray(self._spec.get_wavelengths())
            wl_mask     = (wavelengths >= self._wl_min) & (wavelengths <= self._wl_max)
            wavelengths = wavelengths[wl_mask]
            for i, (pos, delay) in enumerate(
                    zip(self._positions_mm, self._delays_fs)):
                if self._stop:
                    break
                self._stage.move_to(pos)
                actual = self._stage.get_position()

                if not buffer.flush_and_fill(stop_fn):
                    break

                spectrum = buffer.mean().spectrum[wl_mask]
                std      = buffer.std()[wl_mask]
                self.step_done.emit(i, delay, actual, wavelengths, spectrum, std)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ── 2-D scan plot ─────────────────────────────────────────────────────────────

class ScanPlot(QWidget):
    def __init__(self):
        super().__init__()
        self._fig     = Figure(figsize=(6, 5), tight_layout=True)
        self._ax      = self._fig.add_subplot(111)
        self._canvas  = FigureCanvasQTAgg(self._fig)
        self._mesh    = None
        self._colorbar = None
        self._data    = None
        self._delays  = None
        self._wavelengths = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas)

    def init_scan(self, delays_fs: np.ndarray, wavelengths: np.ndarray):
        self._delays      = delays_fs
        self._wavelengths = wavelengths
        self._data     = np.full((len(delays_fs), len(wavelengths)), np.nan)
        self._std_data = np.full((len(delays_fs), len(wavelengths)), np.nan)

        if self._colorbar is not None:
            self._colorbar.remove()
            self._colorbar = None
        self._ax.cla()

        def edges(arr):
            d = np.diff(arr)
            return np.concatenate([[arr[0] - d[0]/2],
                                    arr[:-1] + d/2,
                                    [arr[-1] + d[-1]/2]])

        self._mesh = self._ax.pcolormesh(
            edges(wavelengths), edges(delays_fs), self._data,
            cmap="inferno", shading="flat")
        self._colorbar = self._fig.colorbar(
            self._mesh, ax=self._ax, label="Intensity")
        self._ax.set_xlabel("Wavelength (nm)")
        self._ax.set_ylabel("Delay (fs)")
        self._ax.set_title("Scan")
        self._canvas.draw()

    def update_step(self, index: int, spectrum: np.ndarray, std: np.ndarray):
        if self._data is None:
            return
        self._data[index]     = spectrum
        self._std_data[index] = std
        valid = self._data[~np.all(np.isnan(self._data), axis=1)]
        if valid.size:
            self._mesh.set_array(self._data.ravel())
            self._mesh.set_clim(np.nanmin(valid), np.nanmax(valid))
        self._canvas.draw_idle()

    def get_data(self):
        return self._delays, self._wavelengths, self._data, self._std_data


# ── Main window ────────────────────────────────────────────────────────────────

class ScanWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Delay Scan")
        self._worker = None

        self._status = QStatusBar()
        self.setStatusBar(self._status)

        self._build_ui()

    def _build_ui(self):
        # ── Top-level: stage on left, spec+plot on right ───────────────────────
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(h_splitter)

        # ── Left: stage controls + scan params ────────────────────────────────
        left = QWidget()
        left.setMinimumWidth(360)
        left.setMaximumWidth(440)
        ctrl = QVBoxLayout(left)
        ctrl.setSpacing(10)
        ctrl.setContentsMargins(12, 12, 12, 12)

        self._stage_ctrl = StageControlWidget()
        self._stage_ctrl.status_message.connect(self._status.showMessage)
        self._stage_ctrl.t0_changed.connect(self._update_range_indicator)
        ctrl.addWidget(self._stage_ctrl)

        # Scan params
        scan_group = QGroupBox("Delay Scan")
        scl = QGridLayout(scan_group)

        scl.addWidget(QLabel("Start (fs):"), 0, 0)
        self._scan_start = QDoubleSpinBox()
        self._scan_start.setRange(-100_000, 100_000)
        self._scan_start.setValue(-100_000.0)
        self._scan_start.setDecimals(1)
        self._scan_start.setSuffix(" fs")
        _w = int(self._scan_start.sizeHint().width() * 1.5)
        self._scan_start.setValue(-500.0)
        self._scan_start.setFixedWidth(_w)
        scl.addWidget(self._scan_start, 0, 1)

        scl.addWidget(QLabel("Stop (fs):"), 1, 0)
        self._scan_stop = QDoubleSpinBox()
        self._scan_stop.setRange(-100_000, 100_000)
        self._scan_stop.setValue(500.0)
        self._scan_stop.setDecimals(1)
        self._scan_stop.setSuffix(" fs")
        self._scan_stop.setFixedWidth(_w)
        scl.addWidget(self._scan_stop, 1, 1)

        scl.addWidget(QLabel("Step (fs):"), 2, 0)
        self._scan_step = QDoubleSpinBox()
        self._scan_step.setRange(0.1, 10_000)
        self._scan_step.setValue(50.0)
        self._scan_step.setDecimals(1)
        self._scan_step.setSuffix(" fs")
        self._scan_step.setFixedWidth(_w)
        scl.addWidget(self._scan_step, 2, 1)

        scl.addWidget(QLabel("λ range (nm):"), 3, 0)
        wl_row = QHBoxLayout()
        wl_row.setSpacing(4)
        self._wl_min = QDoubleSpinBox()
        self._wl_min.setRange(100, 3000)
        self._wl_min.setDecimals(1)
        self._wl_min.setValue(300.0)
        self._wl_min.setFixedWidth(80)
        wl_row.addWidget(self._wl_min)
        wl_row.addWidget(QLabel("–"))
        self._wl_max = QDoubleSpinBox()
        self._wl_max.setRange(100, 3000)
        self._wl_max.setDecimals(1)
        self._wl_max.setValue(1100.0)
        self._wl_max.setFixedWidth(80)
        wl_row.addWidget(self._wl_max)
        wl_row.addStretch()
        wl_container = QWidget()
        wl_container.setLayout(wl_row)
        scl.addWidget(wl_container, 3, 1)

        self._scan_preview = QLabel("")
        scl.addWidget(self._scan_preview, 4, 0)
        self._scan_range_indicator = QLabel("●")
        self._scan_range_indicator.setStyleSheet("color: grey;")
        self._scan_range_indicator.setToolTip("Set t0 to check range")
        scl.addWidget(self._scan_range_indicator, 4, 1)

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
        scl.addLayout(btn_row, 5, 0, 1, 2)

        self._progress = QProgressBar()
        scl.addWidget(self._progress, 6, 0, 1, 2)
        self._scan_step_label = QLabel("")
        self._scan_step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scl.addWidget(self._scan_step_label, 7, 0, 1, 2)

        self._save_btn = QPushButton("Save Scan Data…")
        self._save_btn.setFixedHeight(34)
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._do_save)
        scl.addWidget(self._save_btn, 8, 0, 1, 2)

        ctrl.addWidget(scan_group)
        ctrl.addStretch()
        h_splitter.addWidget(left)

        # ── Right: spectrometer on top, scan plot below ────────────────────────
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        self._spec_widget = SpectrometerWidget()
        self._spec_widget.status_message.connect(self._status.showMessage)
        v_splitter.addWidget(self._spec_widget)
        self._plot = ScanPlot()
        v_splitter.addWidget(self._plot)
        v_splitter.setStretchFactor(0, 1)
        v_splitter.setStretchFactor(1, 1)
        h_splitter.addWidget(v_splitter)
        h_splitter.setStretchFactor(1, 1)
        self.resize(1300, 800)

    # ── Scan preview ───────────────────────────────────────────────────────────

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
        self._update_range_indicator()

    def _update_range_indicator(self):
        stage = self._stage_ctrl.stage
        t0_mm = self._stage_ctrl.t0_mm
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

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _do_scan(self):
        stage = self._stage_ctrl.stage
        t0_mm = self._stage_ctrl.t0_mm
        if stage is None or t0_mm is None:
            QMessageBox.warning(self, "Not ready",
                                "Connect a stage and set t0 before scanning.")
            return
        spec = self._spec_widget.spec
        if spec is None:
            QMessageBox.warning(self, "No Spectrometer",
                                "Connect a spectrometer before scanning.")
            return

        delays_fs    = self._scan_delays()
        positions_mm = np.clip(t0_mm + fs_to_mm(delays_fs),
                               stage.min_position, stage.max_position)
        if not np.allclose(t0_mm + fs_to_mm(delays_fs), positions_mm):
            QMessageBox.warning(self, "Range Warning",
                "Some positions are outside the stage range and will be clamped.")

        try:
            wl = np.asarray(spec.get_wavelengths())
        except Exception as e:
            QMessageBox.critical(self, "Spectrometer Error", str(e))
            return

        # Clamp spin box values to actual spectrometer range on first use
        self._wl_min.setRange(float(wl[0]), float(wl[-1]))
        self._wl_max.setRange(float(wl[0]), float(wl[-1]))
        self._wl_min.setValue(max(self._wl_min.value(), float(wl[0])))
        self._wl_max.setValue(min(self._wl_max.value(), float(wl[-1])))

        wl_min = self._wl_min.value()
        wl_max = self._wl_max.value()
        wl_cropped = wl[(wl >= wl_min) & (wl <= wl_max)]
        self._plot.init_scan(delays_fs, wl_cropped)
        self._progress.setMaximum(len(delays_fs))
        self._progress.setValue(0)
        self._scan_step_label.setText("")
        self._save_btn.setEnabled(False)

        self._scan_btn.setEnabled(False)
        self._abort_btn.setEnabled(True)
        self._stage_ctrl.set_busy(True)
        self._status.showMessage("Scanning…")

        # Stop any running free-run / grab worker so the scan has exclusive
        # access to the spectrometer device.
        self._spec_widget.stop_acquisition()

        self._worker = ScanWorker(stage, spec, positions_mm, delays_fs,
                                   n_averages=self._spec_widget.n_averages,
                                   wl_min=wl_min, wl_max=wl_max)
        self._worker.step_done.connect(self._on_scan_step)
        self._worker.finished.connect(self._on_scan_done)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _do_abort(self):
        if isinstance(self._worker, ScanWorker):
            self._worker.stop()
            self._scan_step_label.setText("Aborting…")

    def _on_scan_step(self, index, delay_fs, position_mm, wavelengths, spectrum, std):
        self._progress.setValue(index + 1)
        self._scan_step_label.setText(
            f"Step {index + 1}/{self._progress.maximum()}  "
            f"{delay_fs:+.1f} fs  →  {position_mm:.4f} mm")
        self._stage_ctrl.update_position_display(position_mm)
        self._plot.update_step(index, spectrum, std)

    def _on_scan_done(self):
        self._worker.wait()
        self._worker = None
        self._stage_ctrl.set_busy(False)
        self._scan_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._save_btn.setEnabled(True)
        self._status.showMessage("Scan complete.")
        self._scan_step_label.setText("Done.")

        delays, wavelengths, spectra, _ = self._plot.get_data()
        if spectra is not None and not np.all(np.isnan(spectra)):
            self._analysis_window = ScanAnalysisWindow(delays, wavelengths, spectra)
            self._analysis_window.show()

    def _on_scan_error(self, msg):
        self._worker.wait()
        self._worker = None
        self._stage_ctrl.set_busy(False)
        self._scan_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._status.showMessage(f"Error: {msg}")
        QMessageBox.warning(self, "Scan Error", msg)

    # ── Save ──────────────────────────────────────────────────────────────────

    def _do_save(self):
        delays, wavelengths, spectra, spectra_std = self._plot.get_data()
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
                 spectra_std=spectra_std,
                 t0_mm=np.array([self._stage_ctrl.t0_mm or 0.0]))
        self._status.showMessage(f"Saved to {path}")

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._worker is not None:
            if isinstance(self._worker, ScanWorker):
                self._worker.stop()
            self._worker.wait()
        self._stage_ctrl.shutdown()
        self._spec_widget.shutdown()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = ScanWindow()
    win.show()
    sys.exit(app.exec())

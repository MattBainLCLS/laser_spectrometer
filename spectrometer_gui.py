import os
import sys
import time

import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QDoubleSpinBox, QSpinBox, QStatusBar,
    QMessageBox, QFileDialog, QGroupBox, QCheckBox, QComboBox, QLineEdit,
)
from PyQt6.QtCore import QThread, pyqtSignal

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT

from spectrometers import find_spectrometer


# ---------------------------------------------------------------------------
# Time-domain computation (module-level so workers can call it off-thread)
# ---------------------------------------------------------------------------

def compute_time_domain(wavelengths_nm, spectrum, smooth_sigma=0):
    """
    Reconstruct time-domain pulse intensity from a measured spectrum.

    Steps:
      1. Convert wavelength (nm) → optical frequency (Hz): f = c / λ
      2. Interpolate I(f) onto a uniform frequency grid (N points, spacing df)
      3. Gaussian-smooth I(f) to suppress noise before taking sqrt
      4. Field amplitude:  E(f) = sqrt(I(f))
      5. Zero-pad E(f) symmetrically by PAD_FACTOR to refine dt
      6. Time-domain field: E(t) = IFFT( E_padded(f) )
      7. Return t (fs), |E(t)|², and dt (fs)
    """
    PAD_FACTOR = 5
    c = 2.998e8  # m/s

    wl_m = np.asarray(wavelengths_nm) * 1e-9
    freq = c / wl_m

    order = np.argsort(freq)
    freq_s = freq[order]
    intens_s = np.maximum(np.asarray(spectrum)[order], 0.0)

    n = int(2 ** np.ceil(np.log2(len(freq_s))))
    freq_u = np.linspace(freq_s[0], freq_s[-1], n)
    df = freq_u[1] - freq_u[0]

    intens_u = np.maximum(np.interp(freq_u, freq_s, intens_s), 0.0)

    if smooth_sigma > 0:
        half = int(4 * smooth_sigma)
        k = np.arange(-half, half + 1, dtype=float)
        kernel = np.exp(-k ** 2 / (2 * smooth_sigma ** 2))
        kernel /= kernel.sum()
        intens_u = np.maximum(np.convolve(intens_u, kernel, mode="same"), 0.0)

    E_f = np.sqrt(intens_u)

    m = int(2 ** np.ceil(np.log2(PAD_FACTOR * n)))
    pad_left = (m - n) // 2
    E_f_padded = np.pad(E_f, (pad_left, m - n - pad_left))

    e_t = np.fft.fftshift(np.fft.ifft(np.fft.ifftshift(E_f_padded)))
    t_fs = np.fft.fftshift(np.fft.fftfreq(m, d=df)) * 1e15
    dt_fs = 1.0 / (m * df) * 1e15

    return t_fs, np.abs(e_t) ** 2, dt_fs


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class AcquisitionWorker(QThread):
    spectrum_ready = pyqtSignal(object)
    td_ready = pyqtSignal(object, object, float)  # t_fs, I_t, dt_fs
    error = pyqtSignal(str)

    def __init__(self, spec, continuous=False):
        super().__init__()
        self.spec = spec
        self.continuous = continuous
        self._running = True
        self.td_params = None   # set to (wavelengths, smooth_sigma) to enable TD

    def stop(self):
        self._running = False

    def run(self):
        _spec_last = 0.0
        _td_last = 0.0
        try:
            while self._running:
                self.spec.start_exposure()
                while not self.spec.is_data_ready():
                    if not self._running:
                        return
                    time.sleep(0.01)
                data = self.spec.get_spectrum()

                now = time.monotonic()
                if now - _spec_last >= 0.05:        # cap spectrum plot at 20 fps
                    self.spectrum_ready.emit(data)
                    _spec_last = now

                td_params = self.td_params
                if td_params is not None and now - _td_last >= 0.05:
                    wavelengths, sigma = td_params
                    t_fs, I_t, dt_fs = compute_time_domain(
                        wavelengths, data.spectrum, smooth_sigma=sigma
                    )
                    self.td_ready.emit(t_fs, I_t, dt_fs)
                    _td_last = now

                if not self.continuous:
                    break
        except Exception as e:
            self.error.emit(str(e))


class IntervalWorker(QThread):
    spectrum_ready = pyqtSignal(object, int)
    finished_acquisition = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, spec, interval, duration, output_dir, prefix, fmt):
        super().__init__()
        self.spec = spec
        self.interval = interval
        self.duration = duration
        self.output_dir = output_dir
        self.prefix = prefix
        self.fmt = fmt
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        start = time.monotonic()
        index = 0
        try:
            while self._running:
                if self.duration is not None and (time.monotonic() - start) >= self.duration:
                    break

                t0 = time.monotonic()
                self.spec.start_exposure()
                while not self.spec.is_data_ready():
                    if not self._running:
                        self.finished_acquisition.emit(index)
                        return
                    time.sleep(0.01)

                data = self.spec.get_spectrum()
                self.spectrum_ready.emit(data, index)
                index += 1

                elapsed = time.monotonic() - t0
                remaining = self.interval - elapsed
                if remaining > 0:
                    deadline = time.monotonic() + remaining
                    while time.monotonic() < deadline:
                        if not self._running:
                            self.finished_acquisition.emit(index)
                            return
                        time.sleep(0.05)

        except Exception as e:
            self.error.emit(str(e))
            return

        self.finished_acquisition.emit(index)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class SpectrometerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spectrometer")
        self.setMinimumSize(960, 640)

        self.spec = None
        self.wavelengths = None
        self.worker = None
        self.interval_worker = None
        self.last_data = None
        self.ref_spectrum = None

        self._build_ui()
        self._connect_spectrometer()

    # -----------------------------------------------------------------------
    # UI
    # -----------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(6)

        TD_PANEL_WIDTH = 460
        self._td_panel_width = TD_PANEL_WIDTH

        # --- Plot area (spectrum left, time domain right) ---
        plot_container = QWidget()
        self.plot_layout = QHBoxLayout(plot_container)
        self.plot_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_layout.setSpacing(4)

        # Time domain canvas (hidden until toggled on)
        td_fig = Figure(tight_layout=True)
        self.td_ax = td_fig.add_subplot(111)
        self.td_ax.set_xlabel("Time (fs)")
        self.td_ax.set_ylabel("|E(t)|²  (arb.)")
        self.td_ax.set_title("Time Domain")
        self.td_ax.grid(True)
        self.td_line, = self.td_ax.plot([], [], linewidth=1, color="C1")
        self.td_canvas = FigureCanvasQTAgg(td_fig)
        self.td_toolbar = NavigationToolbar2QT(self.td_canvas, self)

        self.td_panel = QWidget()
        td_panel_layout = QVBoxLayout(self.td_panel)
        td_panel_layout.setContentsMargins(0, 0, 0, 0)
        td_panel_layout.setSpacing(4)
        td_panel_layout.addWidget(self.td_toolbar)
        td_panel_layout.addWidget(self.td_canvas)

        # TD controls row
        td_ctrl = QHBoxLayout()
        td_ctrl.setSpacing(6)
        td_ctrl.addWidget(QLabel("Time window ±"))
        self.td_window_spin = QDoubleSpinBox()
        self.td_window_spin.setRange(1, 100000)
        self.td_window_spin.setDecimals(0)
        self.td_window_spin.setSuffix(" fs")
        self.td_window_spin.setValue(500)
        self.td_window_spin.setFixedWidth(100)
        self.td_window_spin.valueChanged.connect(self._on_td_controls_changed)
        td_ctrl.addWidget(self.td_window_spin)

        td_ctrl.addSpacing(12)
        td_ctrl.addWidget(QLabel("Smoothing σ:"))
        self.td_smooth_spin = QSpinBox()
        self.td_smooth_spin.setRange(0, 50)
        self.td_smooth_spin.setSuffix(" px")
        self.td_smooth_spin.setValue(3)
        self.td_smooth_spin.setFixedWidth(70)
        self.td_smooth_spin.valueChanged.connect(self._on_td_controls_changed)
        td_ctrl.addWidget(self.td_smooth_spin)

        td_ctrl.addSpacing(12)
        self.td_dt_label = QLabel("dt: –")
        td_ctrl.addWidget(self.td_dt_label)

        td_ctrl.addStretch()
        td_panel_layout.addLayout(td_ctrl)

        self.td_panel.setFixedWidth(TD_PANEL_WIDTH)
        self.td_panel.setVisible(False)

        # Spectrum canvas (added first — stays on the left)
        spec_fig = Figure(tight_layout=True)
        self.ax = spec_fig.add_subplot(111)
        self.ax.set_xlabel("Wavelength (nm)")
        self.ax.set_ylabel("Intensity (ADC counts)")
        self.ax.set_title("Spectrum")
        self.ax.grid(True)
        self.ref_line, = self.ax.plot([], [], linewidth=1, color="C1",
                                      alpha=0.8, label="Reference", zorder=1)
        self.line, = self.ax.plot([], [], linewidth=1, label="Live", zorder=2)
        self.canvas = FigureCanvasQTAgg(spec_fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)

        spec_panel = QWidget()
        spec_panel_layout = QVBoxLayout(spec_panel)
        spec_panel_layout.setContentsMargins(0, 0, 0, 0)
        spec_panel_layout.addWidget(self.toolbar)
        spec_panel_layout.addWidget(self.canvas)
        self.plot_layout.addWidget(spec_panel)

        # Time domain panel added after spectrum — appears on the right
        self.plot_layout.addWidget(self.td_panel)

        layout.addWidget(plot_container)

        # --- Basic controls ---
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        ctrl.addWidget(QLabel("Exposure (s):"))
        self.exposure_spin = QDoubleSpinBox()
        self.exposure_spin.setDecimals(4)
        self.exposure_spin.setSingleStep(0.01)
        self.exposure_spin.setValue(0.1)
        self.exposure_spin.setFixedWidth(90)
        ctrl.addWidget(self.exposure_spin)
        ctrl.addSpacing(8)

        self.grab_btn = QPushButton("Grab Spectrum")
        self.grab_btn.setFixedWidth(130)
        self.grab_btn.clicked.connect(self.grab_spectrum)
        ctrl.addWidget(self.grab_btn)

        self.freerun_btn = QPushButton("Start Free Run")
        self.freerun_btn.setFixedWidth(130)
        self.freerun_btn.setCheckable(True)
        self.freerun_btn.clicked.connect(self.toggle_free_run)
        ctrl.addWidget(self.freerun_btn)

        self.td_btn = QPushButton("Show Time Domain")
        self.td_btn.setFixedWidth(150)
        self.td_btn.setCheckable(True)
        self.td_btn.clicked.connect(self.toggle_time_domain)
        ctrl.addWidget(self.td_btn)

        self.pin_btn = QPushButton("Pin Reference")
        self.pin_btn.setFixedWidth(110)
        self.pin_btn.clicked.connect(self.pin_reference)
        ctrl.addWidget(self.pin_btn)

        self.clear_ref_btn = QPushButton("Clear Reference")
        self.clear_ref_btn.setFixedWidth(120)
        self.clear_ref_btn.clicked.connect(self.clear_reference)
        self.clear_ref_btn.setEnabled(False)
        ctrl.addWidget(self.clear_ref_btn)

        self.save_png_btn = QPushButton("Save PNG")
        self.save_png_btn.setFixedWidth(90)
        self.save_png_btn.clicked.connect(self.save_png)
        ctrl.addWidget(self.save_png_btn)

        self.save_csv_btn = QPushButton("Save CSV")
        self.save_csv_btn.setFixedWidth(90)
        self.save_csv_btn.clicked.connect(self.save_csv)
        ctrl.addWidget(self.save_csv_btn)

        ctrl.addStretch()
        layout.addLayout(ctrl)

        # --- Interval acquisition group ---
        group = QGroupBox("Interval Acquisition")
        group_layout = QHBoxLayout(group)
        group_layout.setSpacing(8)

        group_layout.addWidget(QLabel("Interval (s):"))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setDecimals(1)
        self.interval_spin.setRange(0.1, 86400)
        self.interval_spin.setSingleStep(1)
        self.interval_spin.setValue(5)
        self.interval_spin.setFixedWidth(80)
        group_layout.addWidget(self.interval_spin)

        group_layout.addSpacing(8)
        group_layout.addWidget(QLabel("Duration (s):"))
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 86400)
        self.duration_spin.setValue(60)
        self.duration_spin.setFixedWidth(80)
        group_layout.addWidget(self.duration_spin)

        self.indefinite_check = QCheckBox("Indefinite")
        self.indefinite_check.toggled.connect(self.duration_spin.setDisabled)
        group_layout.addWidget(self.indefinite_check)

        group_layout.addSpacing(8)
        group_layout.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["CSV", "PNG", "Both"])
        self.format_combo.setFixedWidth(70)
        group_layout.addWidget(self.format_combo)

        group_layout.addSpacing(8)
        group_layout.addWidget(QLabel("Output folder:"))
        self.outdir_edit = QLineEdit("spectra")
        self.outdir_edit.setFixedWidth(120)
        group_layout.addWidget(self.outdir_edit)

        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setFixedWidth(70)
        self.browse_btn.clicked.connect(self._browse_output_dir)
        group_layout.addWidget(self.browse_btn)

        group_layout.addSpacing(8)
        self.interval_btn = QPushButton("Start")
        self.interval_btn.setFixedWidth(60)
        self.interval_btn.setCheckable(True)
        self.interval_btn.clicked.connect(self.toggle_interval)
        group_layout.addWidget(self.interval_btn)

        self.interval_progress = QLabel("–")
        self.interval_progress.setMinimumWidth(120)
        group_layout.addWidget(self.interval_progress)

        group_layout.addStretch()
        layout.addWidget(group)

        # --- Status bar ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Connecting…")

    # -----------------------------------------------------------------------
    # Connection
    # -----------------------------------------------------------------------

    def _connect_spectrometer(self):
        self.spec = find_spectrometer()
        if self.spec is None:
            QMessageBox.critical(self, "No device", "No supported spectrometer found.\n\nChecked: RGB Photonics Qseries, Avantes.")
            sys.exit(1)

        self.spec.open()

        self.wavelengths = self.spec.get_wavelengths()
        self.ax.set_xlim(self.wavelengths[0], self.wavelengths[-1])

        self.exposure_spin.setMinimum(self.spec.min_exposure_time)
        self.exposure_spin.setMaximum(self.spec.max_exposure_time)

        self.status_bar.showMessage(
            f"Connected: {self.spec.model_name}  |  S/N: {self.spec.serial_number}  |  "
            f"FW: {self.spec.firmware_version}  |  "
            f"Exposure range: {self.spec.min_exposure_time:.4f}–{self.spec.max_exposure_time:.1f} s"
        )

    # -----------------------------------------------------------------------
    # Grab / free-run
    # -----------------------------------------------------------------------

    def _apply_exposure(self):
        self.spec.exposure_time = self.exposure_spin.value()

    def _start_worker(self, continuous):
        self._apply_exposure()
        self.worker = AcquisitionWorker(self.spec, continuous=continuous)
        if self.td_panel.isVisible():
            self.worker.td_params = (self.wavelengths, self.td_smooth_spin.value())
        self.worker.spectrum_ready.connect(self._on_spectrum)
        self.worker.td_ready.connect(self._on_td_ready)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def grab_spectrum(self):
        self.grab_btn.setEnabled(False)
        self._start_worker(continuous=False)

    def toggle_free_run(self, checked):
        if checked:
            self.freerun_btn.setText("Stop Free Run")
            self.grab_btn.setEnabled(False)
            self._start_worker(continuous=True)
        else:
            self.freerun_btn.setText("Start Free Run")
            if self.worker:
                self.worker.stop()

    # -----------------------------------------------------------------------
    # Time domain
    # -----------------------------------------------------------------------

    def toggle_time_domain(self, checked):
        self.td_panel.setVisible(checked)
        self.td_btn.setText("Hide Time Domain" if checked else "Show Time Domain")
        delta = self._td_panel_width + self.plot_layout.spacing()
        self.resize(self.width() + (delta if checked else -delta), self.height())
        if self.worker and self.worker.isRunning():
            self.worker.td_params = (self.wavelengths, self.td_smooth_spin.value()) if checked else None
        if checked and self.last_data is not None:
            self._update_time_domain(self.last_data)

    def _on_td_ready(self, t_fs, I_t, dt_fs):
        """Receive pre-computed TD result from the worker thread and update the plot."""
        self.td_line.set_data(t_fs, I_t)
        self.td_ax.relim()
        self.td_ax.autoscale_view()
        self.td_ax.set_xlim(-self.td_window_spin.value(), self.td_window_spin.value())
        self.td_canvas.draw_idle()
        self.td_dt_label.setText(f"dt: {dt_fs * 1000:.0f} as" if dt_fs < 1 else f"dt: {dt_fs:.2f} fs")

    def _update_time_domain(self, data):
        """On-demand TD update (single grab, toggle-on with existing data, controls changed)."""
        t_fs, I_t, dt_fs = compute_time_domain(
            self.wavelengths, data.spectrum, smooth_sigma=self.td_smooth_spin.value()
        )
        self._on_td_ready(t_fs, I_t, dt_fs)

    def _on_td_controls_changed(self):
        if self.worker and self.worker.isRunning():
            self.worker.td_params = (self.wavelengths, self.td_smooth_spin.value())
        if self.last_data is not None:
            self._update_time_domain(self.last_data)

    # -----------------------------------------------------------------------
    # Interval acquisition
    # -----------------------------------------------------------------------

    def _browse_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select output folder")
        if path:
            self.outdir_edit.setText(path)

    def toggle_interval(self, checked):
        if checked:
            self._start_interval()
        else:
            self._stop_interval()

    def _start_interval(self):
        output_dir = self.outdir_edit.text().strip() or "spectra"
        os.makedirs(output_dir, exist_ok=True)

        self._apply_exposure()
        duration = None if self.indefinite_check.isChecked() else self.duration_spin.value()

        self.interval_worker = IntervalWorker(
            spec=self.spec,
            interval=self.interval_spin.value(),
            duration=duration,
            output_dir=output_dir,
            prefix=self.spec.serial_number + "_",
            fmt=self.format_combo.currentText(),
        )
        self.interval_worker.spectrum_ready.connect(self._on_interval_spectrum)
        self.interval_worker.finished_acquisition.connect(self._on_interval_finished)
        self.interval_worker.error.connect(self._on_error)

        self._set_controls_enabled(False)
        self.interval_btn.setText("Stop")
        self.interval_progress.setText("0 saved")
        self.interval_worker.start()

    def _stop_interval(self):
        if self.interval_worker:
            self.interval_worker.stop()

    def _on_interval_spectrum(self, data, index):
        self._on_spectrum(data)

        output_dir = self.outdir_edit.text().strip() or "spectra"
        ts = data.timestamp.strftime("%Y%m%d_%H%M%S_%f")[:-3]
        stem = os.path.join(output_dir, f"{self.spec.serial_number}_{ts}_{index:04d}")
        fmt = self.format_combo.currentText()

        if fmt in ("CSV", "Both"):
            self._write_csv(stem + ".csv", data)
        if fmt in ("PNG", "Both"):
            self.canvas.figure.savefig(stem + ".png", dpi=150)

        self.interval_progress.setText(f"{index + 1} saved")

    def _on_interval_finished(self, total):
        self.interval_btn.setChecked(False)
        self.interval_btn.setText("Start")
        self._set_controls_enabled(True)
        self.interval_progress.setText(f"{total} saved — done")

    def _set_controls_enabled(self, enabled):
        for w in (self.grab_btn, self.freerun_btn, self.exposure_spin,
                  self.interval_spin, self.duration_spin, self.indefinite_check,
                  self.format_combo, self.outdir_edit, self.browse_btn):
            w.setEnabled(enabled)

    # -----------------------------------------------------------------------
    # Slots
    # -----------------------------------------------------------------------

    def pin_reference(self):
        if self.last_data is None:
            QMessageBox.warning(self, "No data", "Acquire a spectrum first.")
            return
        self.ref_spectrum = self.last_data.spectrum
        self.ref_line.set_data(self.wavelengths, self.ref_spectrum)
        self.canvas.draw_idle()
        self.clear_ref_btn.setEnabled(True)
        self.status_bar.showMessage("Reference pinned.", 3000)

    def clear_reference(self):
        self.ref_spectrum = None
        self.ref_line.set_data([], [])
        self.canvas.draw_idle()
        self.clear_ref_btn.setEnabled(False)
        self.status_bar.showMessage("Reference cleared.", 3000)

    def _on_spectrum(self, data):
        self.last_data = data
        self.line.set_data(self.wavelengths, data.spectrum)
        self.ax.relim()
        self.ax.autoscale_view(scalex=False)
        self.canvas.draw_idle()

        load = data.load_level
        warn = "  ⚠ OVERLOAD — reduce exposure" if load > 1 else ""
        self.status_bar.showMessage(
            f"{self.spec.model_name}  |  S/N: {self.spec.serial_number}  |  "
            f"Exposure: {data.exposure_time:.4f} s  |  Load: {load:.2f}{warn}  |  "
            f"{data.timestamp.strftime('%H:%M:%S.%f')[:-3]}"
        )

    def _on_error(self, msg):
        QMessageBox.critical(self, "Acquisition error", msg)
        self._reset_buttons()

    def _on_worker_finished(self):
        if not self.freerun_btn.isChecked():
            self._reset_buttons()

    def _reset_buttons(self):
        self.freerun_btn.setChecked(False)
        self.freerun_btn.setText("Start Free Run")
        self.grab_btn.setEnabled(True)
        self._set_controls_enabled(True)
        self.interval_btn.setChecked(False)
        self.interval_btn.setText("Start")

    # -----------------------------------------------------------------------
    # Save helpers
    # -----------------------------------------------------------------------

    def _write_csv(self, path, data):
        with open(path, "w") as f:
            f.write(f"# {self.spec.model_name}  S/N: {self.spec.serial_number}\n")
            f.write(f"# Timestamp: {data.timestamp}\n")
            f.write(f"# Exposure: {data.exposure_time} s  Averaging: {data.averaging}\n")
            f.write("wavelength_nm,intensity\n")
            for wl, intensity in zip(self.wavelengths, data.spectrum):
                f.write(f"{wl:.4f},{intensity:.4f}\n")

    def save_png(self):
        self.canvas.figure.savefig("spectrum.png", dpi=150)
        self.status_bar.showMessage("Saved spectrum.png", 3000)

    def save_csv(self):
        if self.last_data is None:
            QMessageBox.warning(self, "No data", "Acquire a spectrum first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "spectrum.csv", "CSV files (*.csv)")
        if not path:
            return
        self._write_csv(path, self.last_data)
        self.status_bar.showMessage(f"Saved {path}", 3000)

    # -----------------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------------

    def closeEvent(self, event):
        for w in (self.worker, self.interval_worker):
            if w:
                w.stop()
                w.wait()
        if self.spec:
            try:
                self.spec.close()
            except Exception:
                pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = SpectrometerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

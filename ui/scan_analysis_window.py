"""
Post-scan analysis window.

Shows the scan data integrated over the spectral axis and lets the user
fit a Gaussian to the resulting time trace.
"""

import numpy as np
from scipy.optimize import curve_fit

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QDoubleSpinBox, QGroupBox,
)
from PyQt6.QtCore import Qt

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT


def gaussian(t, A, t0, sigma, offset):
    return A * np.exp(-0.5 * ((t - t0) / sigma) ** 2) + offset


class ScanAnalysisWindow(QWidget):

    def __init__(self, delays_fs, wavelengths_nm, spectra, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scan Analysis")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(800, 450)

        self._delays      = np.asarray(delays_fs)
        self._wavelengths = np.asarray(wavelengths_nm)
        self._spectra     = np.asarray(spectra)

        # Integrate spectra over wavelength axis; skip NaN rows
        valid_rows = ~np.all(np.isnan(self._spectra), axis=1)
        self._signal = np.full(len(self._delays), np.nan)
        self._signal[valid_rows] = np.trapezoid(
            self._spectra[valid_rows], self._wavelengths, axis=1)

        self._fit_line = None   # matplotlib line for the fit overlay

        self._build_ui()
        self._auto_guess()
        self._do_fit()

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # ── Left: parameters + results ────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(230)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # Starting values
        p_group = QGroupBox("Starting Values")
        p_grid  = QGridLayout(p_group)
        p_grid.setSpacing(6)

        self._p_amplitude = self._spin(-1e12, 1e12, 2)
        self._p_center    = self._spin(-1e6,  1e6,  1, " fs")
        self._p_sigma     = self._spin( 0.1,  1e6,  1, " fs")
        self._p_offset    = self._spin(-1e12, 1e12, 2)

        for row, (lbl, sp) in enumerate([
            ("Amplitude:",  self._p_amplitude),
            ("Center (fs):", self._p_center),
            ("Sigma (fs):",  self._p_sigma),
            ("Offset:",      self._p_offset),
        ]):
            p_grid.addWidget(QLabel(lbl), row, 0)
            p_grid.addWidget(sp, row, 1)

        left_layout.addWidget(p_group)

        self._fit_btn = QPushButton("Fit Gaussian")
        self._fit_btn.setFixedHeight(36)
        self._fit_btn.clicked.connect(self._do_fit)
        left_layout.addWidget(self._fit_btn)

        # Fitted results
        r_group = QGroupBox("Fitted Parameters")
        r_grid  = QGridLayout(r_group)
        r_grid.setSpacing(6)

        self._r_amplitude = QLabel("—")
        self._r_center    = QLabel("—")
        self._r_sigma     = QLabel("—")
        self._r_fwhm      = QLabel("—")
        self._r_offset    = QLabel("—")

        for row, (lbl, val) in enumerate([
            ("Amplitude:",  self._r_amplitude),
            ("Center:",     self._r_center),
            ("Sigma:",      self._r_sigma),
            ("FWHM:",       self._r_fwhm),
            ("Offset:",     self._r_offset),
        ]):
            r_grid.addWidget(QLabel(lbl), row, 0)
            val.setAlignment(Qt.AlignmentFlag.AlignRight |
                             Qt.AlignmentFlag.AlignVCenter)
            r_grid.addWidget(val, row, 1)

        left_layout.addWidget(r_group)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: red;")
        self._status_label.setWordWrap(True)
        left_layout.addWidget(self._status_label)

        left_layout.addStretch()
        layout.addWidget(left)

        # ── Right: plot ───────────────────────────────────────────────────────
        fig = Figure(tight_layout=True)
        self._ax     = fig.add_subplot(111)
        self._canvas = FigureCanvasQTAgg(fig)
        toolbar      = NavigationToolbar2QT(self._canvas, self)

        plot_widget = QWidget()
        pl = QVBoxLayout(plot_widget)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(toolbar)
        pl.addWidget(self._canvas)
        layout.addWidget(plot_widget)

        self._draw_data()

    def _spin(self, lo, hi, decimals, suffix=""):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(decimals)
        sp.setSuffix(suffix)
        sp.setFixedWidth(115)
        return sp

    # ── Auto-guess ─────────────────────────────────────────────────────────────

    def _auto_guess(self):
        valid = ~np.isnan(self._signal)
        if not valid.any():
            return
        s = self._signal[valid]
        d = self._delays[valid]

        offset = float(np.percentile(s, 10))
        amp    = float(s.max() - offset)
        center = float(d[np.argmax(s)])

        above = d[s > offset + amp / 2]
        sigma = float((above[-1] - above[0]) / 2.355) if len(above) > 1 else 100.0
        sigma = max(sigma, 1.0)

        self._p_amplitude.setValue(amp)
        self._p_center.setValue(center)
        self._p_sigma.setValue(sigma)
        self._p_offset.setValue(offset)

    # ── Fit ────────────────────────────────────────────────────────────────────

    def _do_fit(self):
        valid = ~np.isnan(self._signal)
        if valid.sum() < 4:
            self._status_label.setText("Not enough data points to fit.")
            return

        t = self._delays[valid]
        y = self._signal[valid]
        p0 = [
            self._p_amplitude.value(),
            self._p_center.value(),
            self._p_sigma.value(),
            self._p_offset.value(),
        ]

        try:
            popt, _ = curve_fit(
                gaussian, t, y, p0=p0,
                bounds=([-np.inf, -np.inf, 0.01, -np.inf], np.inf),
                maxfev=20000,
            )
        except Exception as e:
            self._status_label.setText(f"Fit failed: {e}")
            self._draw_data()
            return

        A, t0, sigma, offset = popt
        fwhm = 2.355 * abs(sigma)

        self._r_amplitude.setText(f"{A:.4g}")
        self._r_center.setText(f"{t0:.2f} fs")
        self._r_sigma.setText(f"{abs(sigma):.2f} fs")
        self._r_fwhm.setText(f"<b>{fwhm:.2f} fs</b>")
        self._r_offset.setText(f"{offset:.4g}")
        self._status_label.setText("")

        self._draw_data(popt)

    # ── Plot ───────────────────────────────────────────────────────────────────

    def _draw_data(self, popt=None):
        self._ax.cla()
        valid = ~np.isnan(self._signal)
        self._ax.plot(self._delays[valid], self._signal[valid],
                      "o", ms=4, color="C0", label="Data")

        if popt is not None:
            t_fine = np.linspace(self._delays.min(), self._delays.max(), 500)
            self._ax.plot(t_fine, gaussian(t_fine, *popt),
                          "-", color="C1", linewidth=1.5, label="Gaussian fit")

        self._ax.set_xlabel("Delay (fs)")
        self._ax.set_ylabel("Integrated intensity (arb.)")
        self._ax.set_title("Spectrally integrated scan")
        self._ax.legend()
        self._ax.grid(True, alpha=0.3)
        self._canvas.draw()

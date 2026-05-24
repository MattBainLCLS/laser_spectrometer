"""
Picomotor control widgets.

Widget hierarchy
----------------
PicomotorAxisWidget     — single axis: jog, step size, position display
AllAxesWidget           — all detected axes grouped by controller (diagnostic)
MirrorWidget            — H/V pair for one mirror, arrows match orientation

Standalone usage
----------------
    python picomotor_widget.py                      # all mirrors panel (default)
    python picomotor_widget.py --all-axes           # all-axes diagnostic panel
    python picomotor_widget.py --mirror "Mirror 1"  # single mirror from config
    python picomotor_widget.py --axis 106326 1      # single axis by serial+axis
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QStatusBar, QVBoxLayout, QWidget,
)

from hardware.picomotor import Picomotor8742, find_picomotors, MOTOR_NONE
from hardware.picomotor_config import (
    AxisRef, PicomotorConfig, MirrorConfig,
    load_config, save_config, config_path,
)


# ── Worker ────────────────────────────────────────────────────────────────────

class _MoveWorker(QThread):
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, command, *args):
        super().__init__()
        self._command = command
        self._args    = args

    def run(self):
        try:
            self._command(*self._args)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ── PicomotorAxisWidget ───────────────────────────────────────────────────────

class PicomotorAxisWidget(QWidget):
    """
    Baseline widget for one picomotor axis.

    Provides jog buttons, step size control, and a live step-count display.
    The button symbols reflect the axis orientation:
        "horizontal"  →  ◄  ►
        "vertical"    →  ▼  ▲  (negative = down, positive = up)
        "generic"     →  −  +

    Signals
    -------
    status_message  — str: suitable for a host window's status bar
    move_started    — emitted when a jog begins (use to lock sibling axes)
    move_finished   — emitted when a jog completes or errors
    """

    status_message = pyqtSignal(str)
    move_started   = pyqtSignal()
    move_finished  = pyqtSignal()

    _BUTTONS = {
        "horizontal": ("◄", "►"),
        "vertical":   ("▼", "▲"),
        "generic":    ("−", "+"),
    }

    def __init__(self, ctrl: Picomotor8742, axis: int,
                 orientation: str = "generic",
                 label: str | None = None,
                 parent=None):
        super().__init__(parent)
        self._ctrl   = ctrl
        self._axis   = axis
        self._worker = None

        neg_sym, pos_sym = self._BUTTONS.get(orientation, ("−", "+"))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        if label:
            lbl = QLabel(label)
            lbl.setFixedWidth(22)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-weight: bold; color: #888;")
            layout.addWidget(lbl)

        self._neg_btn = QPushButton(neg_sym)
        self._neg_btn.setFixedSize(36, 36)
        self._neg_btn.clicked.connect(self._jog_neg)
        layout.addWidget(self._neg_btn)

        self._step_spin = QSpinBox()
        self._step_spin.setRange(1, 100_000)
        self._step_spin.setValue(100)
        self._step_spin.setSuffix(" steps")
        self._step_spin.setFixedWidth(110)
        layout.addWidget(self._step_spin)

        self._pos_btn = QPushButton(pos_sym)
        self._pos_btn.setFixedSize(36, 36)
        self._pos_btn.clicked.connect(self._jog_pos)
        layout.addWidget(self._pos_btn)

        self._pos_label = QLabel("—")
        self._pos_label.setFixedWidth(80)
        self._pos_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._pos_label.setStyleSheet("color: #5588cc;")
        layout.addWidget(self._pos_label)

        layout.addStretch()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(500)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_enabled(self, enabled: bool):
        for w in (self._neg_btn, self._pos_btn, self._step_spin):
            w.setEnabled(enabled)

    def set_home(self):
        """Zero the step counter at the current position."""
        try:
            self._ctrl.set_home(self._axis)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def shutdown(self):
        self._poll_timer.stop()
        if self._worker is not None:
            self._worker.wait()
            self._worker = None

    # ── Jog ───────────────────────────────────────────────────────────────────

    def _jog_neg(self):
        self._jog(-self._step_spin.value())

    def _jog_pos(self):
        self._jog(self._step_spin.value())

    def _jog(self, steps: int):
        if self._worker is not None:
            return
        self.set_enabled(False)
        self.move_started.emit()
        self._worker = _MoveWorker(self._ctrl.move_relative, self._axis, steps)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self):
        self._cleanup()
        self.set_enabled(True)
        self.move_finished.emit()
        self.status_message.emit("Ready.")

    def _on_error(self, msg: str):
        self._cleanup()
        self.set_enabled(True)
        self.move_finished.emit()
        self.status_message.emit(f"Error: {msg}")
        QMessageBox.warning(self, "Motor Error", msg)

    def _cleanup(self):
        if self._worker is not None:
            self._worker.wait()
            self._worker = None

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll(self):
        if self._worker is not None:
            return
        try:
            pos = self._ctrl.get_position(self._axis)
            self._pos_label.setText(f"{pos:+d} st")
        except Exception:
            pass


# ── AllAxesWidget ─────────────────────────────────────────────────────────────

class AllAxesWidget(QWidget):
    """
    Diagnostic widget showing one PicomotorAxisWidget for every axis that
    has a motor, grouped by controller serial number.

    Discovers and owns its controllers.  Useful for initial setup and
    testing before a config file has been written.
    """

    status_message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._controllers: list[Picomotor8742] = []
        self._axis_widgets: list[PicomotorAxisWidget] = []

        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(8)
        self._layout.addStretch()

        self._build()

    def _build(self):
        self.status_message.emit("Scanning for picomotor controllers…")
        QApplication.processEvents()

        self._controllers = find_picomotors(run_motor_check=True)

        if not self._controllers:
            self._layout.insertWidget(0, QLabel("No 8742 controllers found."))
            self.status_message.emit("No picomotor controllers found.")
            return

        for ctrl in self._controllers:
            axes_with_motors = [
                ax for ax in (1, 2, 3, 4)
                if ctrl.motor_type(ax) != MOTOR_NONE
            ]
            if not axes_with_motors:
                continue

            group = QGroupBox(f"Controller  SN {ctrl.serial_number}")
            group_layout = QVBoxLayout(group)
            group_layout.setSpacing(4)

            for axis in axes_with_motors:
                w = PicomotorAxisWidget(
                    ctrl, axis,
                    orientation="generic",
                    label=str(axis))
                w.status_message.connect(self.status_message)
                group_layout.addWidget(w)
                self._axis_widgets.append(w)

            # Insert before the trailing stretch
            self._layout.insertWidget(self._layout.count() - 1, group)

        self.status_message.emit(
            f"Found {len(self._controllers)} controller(s), "
            f"{len(self._axis_widgets)} axis/axes with motors.")

    def shutdown(self):
        for w in self._axis_widgets:
            w.shutdown()
        for ctrl in self._controllers:
            try:
                ctrl.close()
            except Exception:
                pass


# ── MirrorWidget ──────────────────────────────────────────────────────────────

class MirrorWidget(QWidget):
    """
    Control panel for one mirror: horizontal (◄/►) and vertical (▼/▲) axes.

    Axis assignments come from a MirrorConfig entry in the config file.
    The widget only shows the two configured axes — any other axes on the
    same controller are unaffected.

    When one axis is moving the other is locked out.

    Signals
    -------
    status_message  — str
    name_changed    — str: emitted after a successful rename
    """

    status_message = pyqtSignal(str)
    name_changed   = pyqtSignal(str)

    def __init__(self, mirror_cfg: MirrorConfig,
                 controllers: dict[str, Picomotor8742],
                 full_config: PicomotorConfig,
                 parent=None):
        super().__init__(parent)
        self._cfg         = mirror_cfg
        self._full_config = full_config

        h_ctrl = controllers.get(mirror_cfg.horizontal.controller)
        v_ctrl = controllers.get(mirror_cfg.vertical.controller)
        self._connected = h_ctrl is not None and v_ctrl is not None

        self._h_widget: PicomotorAxisWidget | None = None
        self._v_widget: PicomotorAxisWidget | None = None

        self._build_ui(h_ctrl, v_ctrl)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def mirror_name(self) -> str:
        return self._cfg.name

    def shutdown(self):
        if self._h_widget:
            self._h_widget.shutdown()
        if self._v_widget:
            self._v_widget.shutdown()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self, h_ctrl, v_ctrl):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._group = QGroupBox(self._cfg.name)
        outer.addWidget(self._group)
        inner = QVBoxLayout(self._group)
        inner.setSpacing(6)

        if not self._connected:
            missing = []
            if h_ctrl is None:
                missing.append(
                    f"H: controller SN {self._cfg.horizontal.controller}")
            if v_ctrl is None:
                missing.append(
                    f"V: controller SN {self._cfg.vertical.controller}")
            lbl = QLabel("Controller not connected:\n" + "\n".join(missing))
            lbl.setStyleSheet("color: gray;")
            inner.addWidget(lbl)
            return

        self._h_widget = PicomotorAxisWidget(
            h_ctrl, self._cfg.horizontal.axis,
            orientation="horizontal", label="H")
        self._h_widget.status_message.connect(self.status_message)

        self._v_widget = PicomotorAxisWidget(
            v_ctrl, self._cfg.vertical.axis,
            orientation="vertical", label="V")
        self._v_widget.status_message.connect(self.status_message)

        # Lock the other axis while one is moving
        self._h_widget.move_started.connect(
            lambda: self._v_widget.set_enabled(False))
        self._h_widget.move_finished.connect(
            lambda: self._v_widget.set_enabled(True))
        self._v_widget.move_started.connect(
            lambda: self._h_widget.set_enabled(False))
        self._v_widget.move_finished.connect(
            lambda: self._h_widget.set_enabled(True))

        inner.addWidget(self._h_widget)
        inner.addWidget(self._v_widget)

        btn_row = QHBoxLayout()

        home_btn = QPushButton("Set Home")
        home_btn.setFixedHeight(28)
        home_btn.setToolTip("Zero both axis step counters at current position")
        home_btn.clicked.connect(self._do_set_home)
        btn_row.addWidget(home_btn)

        rename_btn = QPushButton("Rename…")
        rename_btn.setFixedHeight(28)
        rename_btn.clicked.connect(self._do_rename)
        btn_row.addWidget(rename_btn)

        btn_row.addStretch()
        inner.addLayout(btn_row)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _do_set_home(self):
        if self._h_widget:
            self._h_widget.set_home()
        if self._v_widget:
            self._v_widget.set_home()
        self.status_message.emit(f"{self._cfg.name}: home set.")

    def _do_rename(self):
        new_name, ok = QInputDialog.getText(
            self, "Rename Mirror", "Mirror name:", text=self._cfg.name)
        if ok and new_name.strip():
            self._cfg.name = new_name.strip()
            self._group.setTitle(self._cfg.name)
            save_config(self._full_config)
            self.name_changed.emit(self._cfg.name)


# ── Config dialog ─────────────────────────────────────────────────────────────

class ConfigDialog(QDialog):
    """
    In-app editor for the picomotor mirror configuration.

    Shows one editable row per mirror.  Axis dropdowns are populated from
    detected controllers; any axis with a motor is offered as an option.

    Parameters
    ----------
    config      : current PicomotorConfig (will not be mutated)
    controllers : dict mapping serial_number → Picomotor8742 (may be empty)
    """

    def __init__(self, config: PicomotorConfig,
                 controllers: dict[str, Picomotor8742],
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure Mirrors")
        self.setMinimumWidth(520)

        # Build the list of (label, serial, axis) for all axes that have motors.
        # Fall back gracefully if no controllers are connected.
        self._axis_options: list[tuple[str, str, int]] = []
        for serial, ctrl in controllers.items():
            for ax in (1, 2, 3, 4):
                if ctrl.motor_type(ax) != MOTOR_NONE:
                    self._axis_options.append(
                        (f"SN {serial} — axis {ax}", serial, ax))

        self._rows: list[dict] = []   # one dict per mirror row

        outer = QVBoxLayout(self)

        # ── Mirror rows (scrollable) ──────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setSpacing(8)
        self._rows_layout.addStretch()
        scroll.setWidget(self._rows_widget)
        outer.addWidget(scroll)

        # Populate from existing config
        for m in config.mirrors:
            self._add_row(m.name, m.horizontal, m.vertical)

        # ── Add mirror button ─────────────────────────────────────────────────
        add_btn = QPushButton("+ Add Mirror")
        add_btn.setFixedHeight(28)
        add_btn.clicked.connect(lambda: self._add_row())
        outer.addWidget(add_btn)

        # ── OK / Cancel ───────────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

    # ── Row management ────────────────────────────────────────────────────────

    def _add_row(self, name: str = "New Mirror",
                 h_ref: AxisRef | None = None,
                 v_ref: AxisRef | None = None):
        row_widget = QGroupBox()
        row_widget.setFlat(True)
        form = QFormLayout(row_widget)
        form.setContentsMargins(4, 4, 4, 4)
        form.setSpacing(6)

        name_edit = QLineEdit(name)
        form.addRow("Name:", name_edit)

        h_combo = self._make_axis_combo(h_ref)
        form.addRow("Horizontal axis:", h_combo)

        v_combo = self._make_axis_combo(v_ref)
        form.addRow("Vertical axis:", v_combo)

        remove_btn = QPushButton("Remove")
        remove_btn.setFixedHeight(24)
        form.addRow("", remove_btn)

        row = {"widget": row_widget, "name": name_edit,
               "h": h_combo, "v": v_combo}
        self._rows.append(row)
        remove_btn.clicked.connect(lambda: self._remove_row(row))

        # Insert before the trailing stretch
        self._rows_layout.insertWidget(self._rows_layout.count() - 1, row_widget)

    def _remove_row(self, row: dict):
        row["widget"].deleteLater()
        self._rows_layout.removeWidget(row["widget"])
        self._rows.remove(row)

    def _make_axis_combo(self, ref: AxisRef | None) -> QComboBox:
        combo = QComboBox()
        if not self._axis_options:
            combo.addItem("(no controllers detected)", None)
            combo.setEnabled(False)
            return combo

        for label, serial, axis in self._axis_options:
            combo.addItem(label, (serial, axis))

        if ref is not None:
            for i, (_, serial, axis) in enumerate(self._axis_options):
                if serial == ref.controller and axis == ref.axis:
                    combo.setCurrentIndex(i)
                    break

        return combo

    # ── Accept ────────────────────────────────────────────────────────────────

    def _on_accept(self):
        mirrors = []
        for row in self._rows:
            name = row["name"].text().strip()
            if not name:
                QMessageBox.warning(self, "Validation",
                                    "Mirror name must not be empty.")
                return
            h_data = row["h"].currentData()
            v_data = row["v"].currentData()
            if h_data is None or v_data is None:
                QMessageBox.warning(self, "Validation",
                                    f"'{name}': both axes must be assigned.")
                return
            mirrors.append(MirrorConfig(
                name       = name,
                horizontal = AxisRef(h_data[0], h_data[1]),
                vertical   = AxisRef(v_data[0], v_data[1]),
            ))
        self._result_config = PicomotorConfig(mirrors=mirrors, stages=[])
        self.accept()

    def result_config(self) -> PicomotorConfig:
        """Call after exec() == Accepted to get the edited config."""
        return self._result_config


# ── AllMirrorsPanel ───────────────────────────────────────────────────────────

class AllMirrorsPanel(QWidget):
    """
    Shows one MirrorWidget per mirror defined in the config file.

    Axes not referenced in the config are simply ignored — the config is
    authoritative about which axes form mirrors and which are unused.
    """

    status_message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._controllers: dict[str, Picomotor8742] = {}
        self._mirror_widgets: list[MirrorWidget] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Beam Steering")
        font = QFont(); font.setBold(True); font.setPointSize(11)
        title.setFont(font)
        header.addWidget(title)
        header.addStretch()
        cfg_btn = QPushButton("Configure…")
        cfg_btn.setFixedHeight(26)
        cfg_btn.clicked.connect(self._configure)
        header.addWidget(cfg_btn)
        reload_btn = QPushButton("Reload")
        reload_btn.setFixedHeight(26)
        reload_btn.clicked.connect(self._reload)
        header.addWidget(reload_btn)
        outer.addLayout(header)

        self._mirror_layout = QVBoxLayout()
        self._mirror_layout.setSpacing(8)
        outer.addLayout(self._mirror_layout)
        outer.addStretch()

        self._discover_and_build()

    @property
    def mirror_widgets(self) -> list[MirrorWidget]:
        return list(self._mirror_widgets)

    def _discover_and_build(self):
        self._shutdown_mirrors()

        self.status_message.emit("Connecting to picomotor controllers…")
        QApplication.processEvents()

        controllers = find_picomotors(run_motor_check=True)
        self._controllers = {c.serial_number: c for c in controllers}

        cfg = self._load_or_create_config()
        if cfg is None:
            return

        for mirror_cfg in cfg.mirrors:
            w = MirrorWidget(mirror_cfg, self._controllers, cfg, self)
            w.status_message.connect(self.status_message)
            self._mirror_layout.addWidget(w)
            self._mirror_widgets.append(w)

        n = len(self._controllers)
        m = len(self._mirror_widgets)
        self.status_message.emit(
            f"{n} controller(s), {m} mirror(s) configured.")

    def _configure(self):
        cfg = load_config()
        dlg = ConfigDialog(cfg, self._controllers, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_cfg = dlg.result_config()
            save_config(new_cfg)
            self._reload()

    def _load_or_create_config(self) -> PicomotorConfig | None:
        path = config_path()
        if not path.exists():
            # First run — open config dialog immediately
            dlg = ConfigDialog(PicomotorConfig(), self._controllers, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            cfg = dlg.result_config()
            save_config(cfg)
            return cfg
        return load_config()

    def _reload(self):
        for ctrl in self._controllers.values():
            try:
                ctrl.close()
            except Exception:
                pass
        self._controllers.clear()
        self._discover_and_build()

    def _shutdown_mirrors(self):
        for w in self._mirror_widgets:
            w.shutdown()
            self._mirror_layout.removeWidget(w)
            w.deleteLater()
        self._mirror_widgets.clear()

    def shutdown(self):
        self._shutdown_mirrors()
        for ctrl in self._controllers.values():
            try:
                ctrl.close()
            except Exception:
                pass
        self._controllers.clear()


# ── Standalone windows ────────────────────────────────────────────────────────

class AxisWindow(QMainWindow):
    """Standalone window for a single axis."""
    def __init__(self, serial: str, axis: int):
        super().__init__()
        self.setWindowTitle(f"Axis  SN {serial}  axis {axis}")
        self.setMinimumWidth(360)
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        self._ctrl = Picomotor8742(serial=serial)
        try:
            self._ctrl.connect()
        except RuntimeError as e:
            QMessageBox.critical(None, "Connection Error", str(e))
            raise SystemExit(1)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)

        group = QGroupBox(f"SN {serial}  —  axis {axis}")
        inner = QVBoxLayout(group)

        self._axis_widget = PicomotorAxisWidget(
            self._ctrl, axis, orientation="generic", label=str(axis))
        self._axis_widget.status_message.connect(self._status_bar.showMessage)
        inner.addWidget(self._axis_widget)

        home_btn = QPushButton("Set Home")
        home_btn.setFixedHeight(28)
        home_btn.clicked.connect(self._axis_widget.set_home)
        inner.addWidget(home_btn)

        layout.addWidget(group)
        layout.addStretch()

    def closeEvent(self, event):
        self._axis_widget.shutdown()
        self._ctrl.close()
        super().closeEvent(event)


class MirrorWindow(QMainWindow):
    """Standalone window for a single mirror from the config."""
    def __init__(self, mirror_cfg: MirrorConfig,
                 controllers: dict[str, Picomotor8742],
                 full_config: PicomotorConfig):
        super().__init__()
        self.setWindowTitle(mirror_cfg.name)
        self.setMinimumWidth(380)
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._controllers = controllers

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)

        self._widget = MirrorWidget(mirror_cfg, controllers, full_config, self)
        self._widget.status_message.connect(self._status_bar.showMessage)
        self._widget.name_changed.connect(self.setWindowTitle)
        layout.addWidget(self._widget)
        layout.addStretch()

    def closeEvent(self, event):
        self._widget.shutdown()
        for ctrl in self._controllers.values():
            try:
                ctrl.close()
            except Exception:
                pass
        super().closeEvent(event)


class AllAxesWindow(QMainWindow):
    """Standalone diagnostic window showing all connected axes."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Picomotor — All Axes")
        self.setMinimumWidth(400)
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)

        self._widget = AllAxesWidget(self)
        self._widget.status_message.connect(self._status_bar.showMessage)
        layout.addWidget(self._widget)

    def closeEvent(self, event):
        self._widget.shutdown()
        super().closeEvent(event)


class AllMirrorsWindow(QMainWindow):
    """Standalone window showing all configured mirrors."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Picomotor Steering")
        self.setMinimumWidth(400)
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)

        self._panel = AllMirrorsPanel(self)
        self._panel.status_message.connect(self._status_bar.showMessage)
        layout.addWidget(self._panel)

    def closeEvent(self, event):
        self._panel.shutdown()
        super().closeEvent(event)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Picomotor control widgets")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--mirrors", action="store_true",
        help="Show all configured mirrors (default)")
    group.add_argument(
        "--all-axes", action="store_true",
        help="Show all connected axes — diagnostic view")
    group.add_argument(
        "--mirror", metavar="NAME",
        help="Show one mirror by name (e.g. --mirror 'Mirror 1')")
    group.add_argument(
        "--axis", nargs=2, metavar=("SERIAL", "AXIS"),
        help="Show one axis (e.g. --axis 106326 1)")
    args = parser.parse_args()

    app = QApplication(sys.argv)

    if args.axis:
        serial, axis_str = args.axis
        win = AxisWindow(serial, int(axis_str))

    elif args.mirror:
        cfg = load_config()
        matches = [m for m in cfg.mirrors if m.name == args.mirror]
        if not matches:
            names = ", ".join(f'"{m.name}"' for m in cfg.mirrors) or "(none)"
            print(f"Mirror '{args.mirror}' not found. Available: {names}")
            raise SystemExit(1)
        controllers = find_picomotors(run_motor_check=True)
        ctrl_dict = {c.serial_number: c for c in controllers}
        win = MirrorWindow(matches[0], ctrl_dict, load_config())

    elif args.all_axes:
        win = AllAxesWindow()

    else:
        win = AllMirrorsWindow()

    win.show()
    sys.exit(app.exec())

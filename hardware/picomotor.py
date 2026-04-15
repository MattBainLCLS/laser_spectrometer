"""
New Focus 8742 four-axis open-loop picomotor controller.

Communicates directly via pyusb — no drivers, no SDK required.

Protocol notes (hard-won — do not change without testing on hardware):
  - Command terminator is \\r (CR), NOT \\n.  The manual says LF; it is wrong.
  - Do NOT pass a timeout to ep_in.read() — on macOS it silently times out
    even when data is ready.  Use the default (infinite) and rely on the
    50 ms send→recv delay instead.
  - Send only raw command bytes — no zero-padding to 64 bytes.
  - On macOS, is_kernel_driver_active() raises [Errno 2] — skip it.
  - Explicitly claim the USB interface before reading.
  - Responses are terminated with \\r\\n; strip all trailing whitespace/nulls.
  - QM? reads cached memory.  Always call motor_check() after connecting so
    that motor_type() returns valid results.
  - PA/PR/MV are silently ignored (with an error queued) if motion is in
    progress on that axis.  Poll is_moving() or wait for move completion
    before issuing a new move command.
"""

import sys
import time

import usb.core
import usb.util


# ── Constants ─────────────────────────────────────────────────────────────────

_VENDOR_ID  = 0x104D   # Newport / New Focus
_PRODUCT_ID = 0x4000   # 8742 four-axis open-loop controller

MOTOR_NONE     = 0
MOTOR_UNKNOWN  = 1
MOTOR_TINY     = 2
MOTOR_STANDARD = 3

_MOTOR_NAMES = {
    MOTOR_NONE:     "none",
    MOTOR_UNKNOWN:  "unknown",
    MOTOR_TINY:     "tiny",
    MOTOR_STANDARD: "standard",
}


# ── Controller ────────────────────────────────────────────────────────────────

class Picomotor8742:
    """
    Interface to a single New Focus 8742 four-axis picomotor controller.

    Typical usage
    -------------
    ctrl = Picomotor8742()
    ctrl.connect()                    # open USB, run motor check (~3 s)
    ctrl.move_relative(1, 500)        # axis 1: +500 steps, blocks until done
    ctrl.move_absolute(2, 0)          # axis 2: back to home position
    ctrl.close()

    Or use as a context manager:
        with Picomotor8742() as ctrl:
            ctrl.move_relative(1, 500)

    Parameters
    ----------
    serial : str or None
        If given, connect only to the controller whose *IDN? serial matches.
        If None, connect to the first 8742 found on USB.
    """

    def __init__(self, serial: str | None = None):
        self._target_serial = serial
        self._dev    = None
        self._ep_out = None
        self._ep_in  = None
        self._serial = None   # populated after connect()

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def serial_number(self) -> str | None:
        """Controller serial number (e.g. '106326'), or None if not connected."""
        return self._serial

    @property
    def is_connected(self) -> bool:
        return self._dev is not None

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self, run_motor_check: bool = True):
        """
        Open the USB connection and optionally run a motor check.

        Parameters
        ----------
        run_motor_check : bool
            If True (default), run MC so that motor_type() returns valid
            results.  Takes ~3 seconds.  Set False only if motor types are
            already saved in the controller's non-volatile memory (SM was
            previously issued after an MC run).
        """
        devices = list(
            usb.core.find(idVendor=_VENDOR_ID, idProduct=_PRODUCT_ID,
                          find_all=True) or [])
        if not devices:
            raise RuntimeError(
                "No 8742 controller found — is it plugged in and powered on?")

        matched = False
        for dev in devices:
            self._open_usb(dev)
            serial = self._read_serial()
            if self._target_serial is None or serial == self._target_serial:
                self._serial = serial
                matched = True
                break
            # Wrong controller — release and try next
            self._close_usb()

        if not matched:
            raise RuntimeError(
                f"8742 with serial '{self._target_serial}' not found.")

        if run_motor_check:
            self.motor_check()

    def close(self):
        self._close_usb()

    # ── USB setup/teardown ────────────────────────────────────────────────────

    def _open_usb(self, dev):
        dev.set_configuration()
        cfg  = dev.get_active_configuration()
        intf = cfg[(0, 0)]

        # macOS: is_kernel_driver_active() raises [Errno 2] — skip on Darwin
        if sys.platform != "darwin":
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)

        usb.util.claim_interface(dev, intf.bInterfaceNumber)

        self._dev    = dev
        self._ep_out = usb.util.find_descriptor(intf, custom_match=lambda e:
            usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_OUT)
        self._ep_in  = usb.util.find_descriptor(intf, custom_match=lambda e:
            usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_IN)

        # Clear any stale bytes left in the receive buffer from a previous
        # session.  clear_halt() resets the endpoint data toggle and discards
        # buffered data in the host controller's queue.
        try:
            self._ep_in.clear_halt()
        except Exception:
            pass

    def _close_usb(self):
        if self._dev is not None:
            try:
                usb.util.dispose_resources(self._dev)
            except Exception:
                pass
            self._dev    = None
            self._ep_out = None
            self._ep_in  = None

    # ── Low-level I/O ─────────────────────────────────────────────────────────

    def _send(self, cmd: str):
        """Send a command.  Terminator is CR (\\r), NOT LF — the manual is wrong."""
        self._ep_out.write((cmd.strip() + "\r").encode("ascii"))

    def _recv(self) -> str:
        """
        Read one response from the device.

        No timeout argument is passed to ep_in.read() — on macOS an explicit
        timeout causes silent failures even when data is present.
        Responses are \\r\\n terminated; strip all trailing whitespace and nulls.
        """
        raw = bytes(self._ep_in.read(100))
        return raw.rstrip(b"\r\n\x00 ").decode("ascii", errors="replace")

    def _query(self, cmd: str) -> str:
        """Send a command and return the response."""
        self._send(cmd)
        time.sleep(0.05)
        return self._recv()

    # ── Identification ────────────────────────────────────────────────────────

    def _read_serial(self) -> str:
        """
        Parse the serial number from *IDN?.

        Response format: "New_Focus 8742 vY.Y mm/dd/yy, SNxxxxxx"
        The serial number is the last whitespace-delimited token with the
        "SN" prefix stripped.

        Retries up to three times in case the first read returns stale data
        that clear_halt() didn't fully discard.
        """
        for _ in range(3):
            idn = self._query("*IDN?")
            if "New_Focus" in idn or "New Focus" in idn:
                token = idn.split()[-1]
                return token[2:] if token.upper().startswith("SN") else token
            # Response doesn't look like an IDN reply — stale byte or noise.
            # The next iteration will send a fresh *IDN? and read again.
            time.sleep(0.05)
        raise RuntimeError(
            f"Could not read controller identity — unexpected response: {idn!r}")

    # ── Motor check ───────────────────────────────────────────────────────────

    def motor_check(self):
        """
        Run MC — physically pulses all axes to detect connected motor types.

        Must be called after connecting (or after changing motors) so that
        motor_type() returns valid results.  Takes ~3 seconds.

        Note: motor type changes from MC are not automatically saved to
        non-volatile memory.  Call save_settings() afterwards if you want
        them to persist across power cycles.
        """
        self._send("MC")
        time.sleep(3.0)

    def save_settings(self):
        """Save current settings (motor types, velocity, acceleration) to
        non-volatile memory (SM)."""
        self._send("SM")

    # ── Axis queries ──────────────────────────────────────────────────────────

    def motor_type(self, axis: int) -> int:
        """
        Return the motor type for *axis* (1–4).

        Returns one of: MOTOR_NONE, MOTOR_UNKNOWN, MOTOR_TINY, MOTOR_STANDARD.
        Reads cached memory — call motor_check() first if motors may have changed.
        """
        _check_axis(axis)
        try:
            return int(self._query(f"{axis}QM?"))
        except ValueError:
            return MOTOR_NONE

    def motor_type_name(self, axis: int) -> str:
        """Human-readable motor type string for *axis*."""
        return _MOTOR_NAMES.get(self.motor_type(axis), "unknown")

    def get_position(self, axis: int) -> int:
        """
        Return the step-counter value for *axis* (steps from home).

        This is the controller's internal step count — not a physical position.
        It resets to 0 on power-cycle or RS/RST reset unless DH was used.
        """
        _check_axis(axis)
        result = self._query(f"{axis}TP?")
        try:
            return int(result)
        except ValueError:
            raise RuntimeError(
                f"Unexpected position response for axis {axis}: {result!r}")

    def get_error(self) -> tuple[int, str]:
        """
        Return (error_code, error_message) from TB?.

        Returns (0, 'NO ERROR DETECTED') if no error.
        The error buffer is a 10-element FIFO — each call pops one entry.
        """
        response = self._query("TB?")
        if "," in response:
            code_str, msg = response.split(",", 1)
            try:
                return int(code_str.strip()), msg.strip()
            except ValueError:
                pass
        return -1, response

    def is_moving(self, axis: int) -> bool:
        """Return True if *axis* is currently in motion (MD? == 0)."""
        _check_axis(axis)
        return self._query(f"{axis}MD?").strip() == "0"

    # ── Motion commands ───────────────────────────────────────────────────────

    def set_home(self, axis: int):
        """
        Zero the step counter at the current position (DH — soft home).

        After this, get_position() returns 0 and move_absolute() uses this
        position as the origin.
        """
        _check_axis(axis)
        self._send(f"{axis}DH")

    def move_relative(self, axis: int, steps: int, timeout: float = 30.0):
        """
        Move *axis* by *steps* relative to the current position (PR).

        Blocks until motion completes or *timeout* seconds elapse.
        Raises TimeoutError on timeout.
        """
        _check_axis(axis)
        self._send(f"{axis}PR{steps}")
        self._wait_motion_done(axis, timeout)

    def move_absolute(self, axis: int, steps: int, timeout: float = 30.0):
        """
        Move *axis* to *steps* relative to the home position (PA).

        Blocks until motion completes or *timeout* seconds elapse.
        Raises TimeoutError on timeout.
        """
        _check_axis(axis)
        self._send(f"{axis}PA{steps}")
        self._wait_motion_done(axis, timeout)

    def jog(self, axis: int, direction: int):
        """
        Start jogging *axis* indefinitely (MV).

        Parameters
        ----------
        direction : +1 to jog forward, -1 to jog in reverse.

        Call stop() or abort() to halt.
        """
        _check_axis(axis)
        if direction not in (1, -1):
            raise ValueError("direction must be +1 or -1")
        self._send(f"{axis}MV{'+' if direction > 0 else '-'}")

    def stop(self, axis: int):
        """Stop *axis* with deceleration (ST)."""
        _check_axis(axis)
        self._send(f"{axis}ST")

    def abort(self):
        """Emergency-stop all axes immediately, without deceleration (AB)."""
        self._send("AB")

    def _wait_motion_done(self, axis: int, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_moving(axis):
                return
            time.sleep(0.05)
        raise TimeoutError(
            f"Axis {axis} did not finish moving within {timeout:.0f} s")


# ── PicomotorStage — StageBase wrapper ───────────────────────────────────────

from hardware.stages import StageBase   # noqa: E402  (local import after class def)


class PicomotorStage(StageBase):
    """
    Wraps one axis of a Picomotor8742 as a StageBase.

    Allows a picomotor-driven linear actuator to be used wherever a KDC101
    or Xeryon stage is expected (StageControlWidget, FROG scan loop, etc.).

    The "home" operation zeros the step counter at the current physical
    position (soft home via DH) rather than seeking a hardware limit switch.
    This is semantically equivalent for delay-line use: call home() to define
    the zero of the mm scale, then use move_to() for absolute positioning.

    Parameters
    ----------
    controller : Picomotor8742
        An already-connected controller instance.
    axis : int
        Axis number (1–4) on that controller.
    steps_per_mm : float
        Conversion factor for the attached linear actuator.
    min_mm, max_mm : float
        Software travel limits in mm.
    name : str
        Human-readable label shown in the GUI.
    """

    def __init__(self, controller: Picomotor8742, axis: int,
                 steps_per_mm: float,
                 min_mm: float, max_mm: float,
                 name: str = "Picomotor stage"):
        self._ctrl         = controller
        self._axis         = axis
        self._steps_per_mm = steps_per_mm
        self._min_mm       = min_mm
        self._max_mm       = max_mm
        self._name         = name
        self._homed        = False

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def min_position(self) -> float:
        return self._min_mm

    @property
    def max_position(self) -> float:
        return self._max_mm

    def open(self):
        # Controller is already connected before constructing this object.
        pass

    def close(self):
        pass

    def home(self):
        """Zero the step counter at the current position (soft home)."""
        self._ctrl.set_home(self._axis)
        self._homed = True

    def is_homed(self) -> bool:
        return self._homed

    def move_to(self, position_mm: float):
        position_mm = max(self._min_mm, min(self._max_mm, position_mm))
        steps = int(round(position_mm * self._steps_per_mm))
        self._ctrl.move_absolute(self._axis, steps)

    def get_position(self) -> float:
        return self._ctrl.get_position(self._axis) / self._steps_per_mm


# ── Discovery ─────────────────────────────────────────────────────────────────

def find_picomotors(run_motor_check: bool = True) -> list[Picomotor8742]:
    """
    Return a list of Picomotor8742 instances, one per connected controller.

    Each controller is already connected.  Returns an empty list if none are
    found (does not raise).

    Parameters
    ----------
    run_motor_check : bool
        If True (default), run MC on each controller after connecting.
    """
    devices = list(
        usb.core.find(idVendor=_VENDOR_ID, idProduct=_PRODUCT_ID,
                      find_all=True) or [])

    controllers = []
    for dev in devices:
        ctrl = Picomotor8742()
        try:
            ctrl._open_usb(dev)
            ctrl._serial = ctrl._read_serial()
            if run_motor_check:
                ctrl.motor_check()
            controllers.append(ctrl)
        except Exception:
            ctrl._close_usb()

    return controllers


# ── Helpers ───────────────────────────────────────────────────────────────────

def _check_axis(axis: int):
    if axis not in (1, 2, 3, 4):
        raise ValueError(f"Axis must be 1–4, got {axis}")

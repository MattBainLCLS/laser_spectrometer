"""
Hardware abstraction layer for motion stages.

Provides a unified interface over different stage controllers.
Add new hardware by subclassing StageBase and registering a
discovery call in find_stage().
"""

import os
import sys
import struct
import time
from abc import ABC, abstractmethod

import usb.core


# ── Common base class ─────────────────────────────────────────────────────────

class StageBase(ABC):

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    @abstractmethod
    def min_position(self) -> float:
        """Minimum travel position in mm."""
        ...

    @property
    @abstractmethod
    def max_position(self) -> float:
        """Maximum travel position in mm."""
        ...

    @abstractmethod
    def open(self): ...

    @abstractmethod
    def close(self): ...

    @abstractmethod
    def home(self): ...

    @abstractmethod
    def move_to(self, position_mm: float): ...

    @abstractmethod
    def get_position(self) -> float: ...

    @abstractmethod
    def is_homed(self) -> bool: ...


# ── Thorlabs KDC101 implementation ────────────────────────────────────────────

class KDC101Stage(StageBase):
    """
    Thorlabs KDC101 brushed DC servo controller.

    Communicates directly via pyusb using the FTDI USB serial protocol and
    the Thorlabs APT protocol — no VCP driver or FTDI library required.

    Supported stages (scale presets):
        "MTS25-Z8"  — 25 mm travel, 34304 counts/mm
        "MTS50-Z8"  — 50 mm travel, 34304 counts/mm
        "Z825B"     — 25 mm travel, 34304 counts/mm
    """

    _SCALES = {
        "MTS25-Z8": (34304.0, 25.0),
        "MTS50-Z8": (34304.0, 50.0),
        "Z825B":    (34304.0, 25.0),
    }

    # USB identifiers
    _VENDOR_ID  = 0x0403
    _PRODUCT_ID = 0xfaf0
    _EP_OUT     = 0x02
    _EP_IN      = 0x81

    # FTDI control requests
    _FTDI_OUT          = 0x40
    _SIO_RESET         = 0x00
    _SIO_SET_MODEM_CTRL= 0x01
    _SIO_SET_FLOW_CTRL = 0x02
    _SIO_SET_BAUDRATE  = 0x03
    _SIO_SET_DATA      = 0x04

    # APT message IDs
    _MGMSG_HW_REQ_INFO        = 0x0005
    _MGMSG_HW_GET_INFO        = 0x0006
    _MGMSG_MOT_MOVE_HOME      = 0x0443
    _MGMSG_MOT_MOVE_HOMED     = 0x0444
    _MGMSG_MOT_MOVE_ABSOLUTE  = 0x0453
    _MGMSG_MOT_MOVE_COMPLETED = 0x0464
    _MGMSG_MOT_REQ_POSCOUNTER = 0x0411
    _MGMSG_MOT_GET_POSCOUNTER = 0x0412

    _DEST   = 0x50
    _SOURCE = 0x01
    _CHAN   = 0x01

    def __init__(self, scale: str = "MTS25-Z8"):
        if scale not in self._SCALES:
            raise ValueError(
                f"Unknown scale '{scale}'. "
                f"Choose from: {list(self._SCALES)}")
        self._counts_per_mm, self._max_pos = self._SCALES[scale]
        self._scale_name = scale
        self._dev = None
        self._homed = False

    # ── StageBase properties ──────────────────────────────────────────────────

    @property
    def model_name(self) -> str:
        return f"Thorlabs KDC101 ({self._scale_name})"

    @property
    def min_position(self) -> float:
        return 0.0

    @property
    def max_position(self) -> float:
        return self._max_pos

    # ── Connection ────────────────────────────────────────────────────────────

    def open(self):
        dev = usb.core.find(idVendor=self._VENDOR_ID,
                            idProduct=self._PRODUCT_ID)
        if dev is None:
            raise RuntimeError(
                "KDC101 not found — is it plugged in and powered on?")
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
        dev.set_configuration()

        self._dev = dev
        self._ftdi_init()

    def close(self):
        self._dev = None

    def _ftdi_init(self, baudrate=115200):
        self._ftdi_ctrl(self._SIO_RESET, 0, 0)
        time.sleep(0.05)
        divisor = round(3_000_000 / baudrate)
        self._ftdi_ctrl(self._SIO_SET_BAUDRATE, divisor, 0)
        self._ftdi_ctrl(self._SIO_SET_DATA, 0x0008, 0)
        self._ftdi_ctrl(self._SIO_SET_FLOW_CTRL, 0, 0x0101)
        self._ftdi_ctrl(self._SIO_SET_MODEM_CTRL, 0x0303, 0)
        time.sleep(0.1)
        self._ftdi_ctrl(self._SIO_SET_MODEM_CTRL, 0x0200, 0)  # RTS off
        time.sleep(0.05)
        self._ftdi_ctrl(self._SIO_SET_MODEM_CTRL, 0x0202, 0)  # RTS on
        time.sleep(0.1)

    def _ftdi_ctrl(self, request, value, index=0):
        self._dev.ctrl_transfer(self._FTDI_OUT, request, value, index, None)

    # ── Low-level I/O ─────────────────────────────────────────────────────────

    def _write(self, data: bytes):
        self._dev.write(self._EP_OUT, data, timeout=1000)

    def _read(self, size=64, timeout=500) -> bytes:
        buf = b""
        deadline = time.monotonic() + timeout / 1000
        while len(buf) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                chunk = bytes(self._dev.read(
                    self._EP_IN, 64,
                    timeout=max(1, int(remaining * 1000))))
            except usb.core.USBTimeoutError:
                break
            if len(chunk) > 2:
                buf += chunk[2:]   # strip 2-byte FTDI modem-status prefix
        return buf

    # ── APT message helpers ───────────────────────────────────────────────────

    def _short_msg(self, msg_id: int, p1=0, p2=0) -> bytes:
        return struct.pack("<HBBBB", msg_id, p1, p2,
                           self._DEST, self._SOURCE)

    def _long_msg(self, msg_id: int, data: bytes) -> bytes:
        header = struct.pack("<HHBB", msg_id, len(data),
                             self._DEST | 0x80, self._SOURCE)
        return header + data

    def _wait_for(self, target_id: int, timeout: float = 30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self._read(64, timeout=500)
            if len(raw) < 6:
                continue
            for i in range(len(raw) - 5):
                if struct.unpack_from("<H", raw, i)[0] == target_id:
                    return
        raise TimeoutError(
            f"Timed out waiting for APT message 0x{target_id:04X}")

    # ── StageBase commands ────────────────────────────────────────────────────

    def home(self):
        self._write(self._short_msg(self._MGMSG_MOT_MOVE_HOME, self._CHAN))
        self._wait_for(self._MGMSG_MOT_MOVE_HOMED, timeout=60)
        self._homed = True

    def is_homed(self) -> bool:
        return self._homed

    def move_to(self, position_mm: float):
        position_mm = max(self.min_position,
                          min(self.max_position, position_mm))
        counts = int(round(position_mm * self._counts_per_mm))
        data = struct.pack("<Hl", self._CHAN, counts)
        self._write(self._long_msg(self._MGMSG_MOT_MOVE_ABSOLUTE, data))
        self._wait_for(self._MGMSG_MOT_MOVE_COMPLETED, timeout=30)

    def get_position(self) -> float:
        self._write(self._short_msg(
            self._MGMSG_MOT_REQ_POSCOUNTER, self._CHAN, 0))
        raw = self._read(12, timeout=500)
        if len(raw) < 12:
            raise RuntimeError(f"Short position reply ({len(raw)} bytes)")
        counts = struct.unpack_from("<l", raw, 8)[0]
        return counts / self._counts_per_mm

    def get_info(self) -> dict:
        self._write(self._short_msg(self._MGMSG_HW_REQ_INFO))
        raw = self._read(90, timeout=1000)
        if len(raw) < 90:
            return {}
        return {
            "serial":   struct.unpack_from("<I", raw, 6)[0],
            "model":    raw[10:18].decode("ascii", errors="replace").strip("\x00"),
            "firmware": struct.unpack_from("<I", raw, 24)[0],
        }


# ── Xeryon implementation ─────────────────────────────────────────────────────

class XeryonStage(StageBase):
    """
    Xeryon piezo stage controller.

    Wraps the Xeryon Python library (Xeryon/Xeryon.py).
    The controller auto-detects its USB serial port via vendor ID 04D8.

    Parameters
    ----------
    stage_type : str
        Stage model name matching a Xeryon Stage enum member, e.g. "XLS_312".
    axis_letter : str
        Axis identifier as defined in the settings file, e.g. "X".
    settings_file : str | None
        Path to the settings_default.txt file supplied with the stage.
        Defaults to Xeryon/settings_default.txt alongside this file.
    max_position : float
        Travel limit in mm (used when the settings file does not define HLIM).
    """

    _XERYON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor", "xeryon")

    def __init__(self, stage_type: str = "XLS_78_3N",
                 axis_letter: str = "X",
                 settings_file: str | None = None,
                 min_position: float = -12.5,
                 max_position: float = 12.5):
        self._stage_type    = stage_type
        self._axis_letter   = axis_letter
        self._settings_file = settings_file  # None = don't send settings
        self._min_pos       = min_position
        self._max_pos       = max_position
        self._controller    = None
        self._axis          = None

    @property
    def model_name(self) -> str:
        return f"Xeryon {self._stage_type}"

    @property
    def min_position(self) -> float:
        return self._min_pos

    @property
    def max_position(self) -> float:
        return self._max_pos

    def open(self):
        if self._XERYON_DIR not in sys.path:
            sys.path.insert(0, self._XERYON_DIR)

        import Xeryon as _X
        # Suppress the library's console chatter
        _X.OUTPUT_TO_CONSOLE = False

        # If no settings file is provided, rely on settings already stored
        # on the controller and skip sending them on startup.
        if self._settings_file is None:
            _X.AUTO_SEND_SETTINGS = False

        controller = _X.Xeryon()   # port=None → auto-detect via vendor ID 04D8
        stage_enum = getattr(_X.Stage, self._stage_type)
        axis = controller.addAxis(stage_enum, self._axis_letter)
        controller.start(external_settings_default=self._settings_file)

        axis.setUnits(_X.Units.mm)

        self._controller = controller
        self._axis       = axis
        self._X          = _X

    def close(self):
        if self._controller is not None:
            try:
                self._controller.stop()
            except Exception:
                pass
            self._controller = None
            self._axis       = None

    def home(self):
        self._axis.findIndex()

    def is_homed(self) -> bool:
        return self._axis.isEncoderValid()

    def move_to(self, position_mm: float):
        position_mm = max(self.min_position,
                          min(self.max_position, position_mm))
        self._axis.setDPOS(position_mm)

    def get_position(self) -> float:
        return float(self._axis.getEPOS())


# ── Discovery ─────────────────────────────────────────────────────────────────

def find_stage(scale: str = "MTS25-Z8") -> StageBase | None:
    """Try each known stage controller; return an opened instance or None."""

    # Thorlabs KDC101
    try:
        stage = KDC101Stage(scale=scale)
        stage.open()
        return stage
    except Exception:
        pass

    # Xeryon XLS-3 (auto-detects serial port via USB vendor ID 04D8)
    try:
        stage = XeryonStage(stage_type="XLS_78_3N")
        stage.open()
        return stage
    except Exception:
        pass

    return None

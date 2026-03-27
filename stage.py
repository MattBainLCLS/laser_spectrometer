"""
Thorlabs KDC101 + MTS25-Z8 stage controller.

Communicates directly via pyusb using the FTDI USB serial protocol and
Thorlabs APT protocol — no VCP driver or FTDI library required.

Usage:
    python3 stage.py --home
    python3 stage.py --move 10.5
    python3 stage.py --pos
    python3 stage.py --demo
"""

import struct
import time
import argparse
import usb.core
import usb.util

# ── USB / FTDI constants ──────────────────────────────────────────────────────

VENDOR_ID  = 0x0403
PRODUCT_ID = 0xfaf0   # Thorlabs KDC101

EP_OUT = 0x02
EP_IN  = 0x81

FTDI_OUT  = 0x40   # bmRequestType for device→host control transfers
SIO_RESET          = 0x00
SIO_SET_MODEM_CTRL = 0x01
SIO_SET_FLOW_CTRL  = 0x02
SIO_SET_BAUDRATE   = 0x03
SIO_SET_DATA       = 0x04

# ── APT protocol constants ────────────────────────────────────────────────────

DEST   = 0x50   # generic USB unit (KDC101)
SOURCE = 0x01   # host controller
CHAN   = 0x01   # channel 1

MGMSG_HW_REQ_INFO       = 0x0005
MGMSG_HW_GET_INFO       = 0x0006
MGMSG_MOT_MOVE_HOME     = 0x0443
MGMSG_MOT_MOVE_HOMED    = 0x0444
MGMSG_MOT_MOVE_ABSOLUTE = 0x0453
MGMSG_MOT_MOVE_COMPLETED= 0x0464
MGMSG_MOT_REQ_POSCOUNTER= 0x0411
MGMSG_MOT_GET_POSCOUNTER= 0x0412

# MTS25-Z8 encoder scale: 34304 counts per mm
COUNTS_PER_MM = 34304.0


# ── FTDI helpers ──────────────────────────────────────────────────────────────

def _ftdi_ctrl(dev, request, value, index=0):
    dev.ctrl_transfer(FTDI_OUT, request, value, index, None)

def _open_ftdi(baudrate=115200):
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        raise RuntimeError("KDC101 not found — is it plugged in and powered on?")

    # Detach any kernel driver (shouldn't be one on macOS without VCP driver)
    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)
    dev.set_configuration()

    # Reset FTDI chip
    _ftdi_ctrl(dev, SIO_RESET, 0, 0)
    time.sleep(0.05)

    # Baud rate: 3 MHz base clock, divisor = round(3e6 / baudrate)
    divisor = round(3_000_000 / baudrate)
    _ftdi_ctrl(dev, SIO_SET_BAUDRATE, divisor, 0)

    # 8N1
    _ftdi_ctrl(dev, SIO_SET_DATA, 0x0008, 0)

    # RTS/CTS flow control (interface 0 → index low byte = 1)
    _ftdi_ctrl(dev, SIO_SET_FLOW_CTRL, 0, 0x0101)

    # Assert RTS and DTR
    _ftdi_ctrl(dev, SIO_SET_MODEM_CTRL, 0x0303, 0)
    time.sleep(0.1)

    # Cycle RTS low then high (required by KDC101 to wake up)
    _ftdi_ctrl(dev, SIO_SET_MODEM_CTRL, 0x0200, 0)   # RTS off
    time.sleep(0.05)
    _ftdi_ctrl(dev, SIO_SET_MODEM_CTRL, 0x0202, 0)   # RTS on
    time.sleep(0.1)

    return dev


def _write(dev, data: bytes):
    dev.write(EP_OUT, data, timeout=1000)


def _read(dev, size=64, timeout=500) -> bytes:
    """Read from bulk IN endpoint, stripping the 2-byte FTDI modem-status prefix."""
    buf = b""
    deadline = time.monotonic() + timeout / 1000
    while len(buf) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            chunk = bytes(dev.read(EP_IN, 64, timeout=max(1, int(remaining * 1000))))
        except usb.core.USBTimeoutError:
            break
        if len(chunk) > 2:
            buf += chunk[2:]   # strip 2-byte modem status
    return buf


# ── APT message builders ──────────────────────────────────────────────────────

def _short_msg(msg_id: int, p1=0, p2=0) -> bytes:
    return struct.pack("<HBBBB", msg_id, p1, p2, DEST, SOURCE)


def _long_msg(msg_id: int, data: bytes) -> bytes:
    header = struct.pack("<HHBB", msg_id, len(data), DEST | 0x80, SOURCE)
    return header + data


def _parse_header(data: bytes):
    """Return (msg_id, is_long, param1_or_datalen, param2) from a 6-byte APT header."""
    if len(data) < 6:
        return None
    msg_id, p1_or_len, p2, dest = struct.unpack_from("<HHHB", data[:7])[:-1], data[5]
    # Simple approach: just unpack as two separate reads
    msg_id = struct.unpack_from("<H", data, 0)[0]
    is_long = bool(data[4] & 0x80)
    if is_long:
        data_len = struct.unpack_from("<H", data, 2)[0]
        return msg_id, True, data_len, None
    else:
        return msg_id, False, data[2], data[3]


# ── APT commands ──────────────────────────────────────────────────────────────

def get_info(dev) -> dict:
    _write(dev, _short_msg(MGMSG_HW_REQ_INFO))
    raw = _read(dev, 90, timeout=1000)
    if len(raw) < 90:
        return {}
    serial = struct.unpack_from("<I", raw, 6)[0]
    model  = raw[10:18].decode("ascii", errors="replace").strip("\x00")
    fw     = struct.unpack_from("<I", raw, 24)[0]
    return {"serial": serial, "model": model, "firmware": fw}


def home(dev):
    """Send home command and wait for MGMSG_MOT_MOVE_HOMED."""
    _write(dev, _short_msg(MGMSG_MOT_MOVE_HOME, CHAN, 0))
    print("Homing... (this may take ~30s)")
    _wait_for(dev, MGMSG_MOT_MOVE_HOMED, timeout=60)
    print("Homed.")


def move_to(dev, position_mm: float):
    """Move to an absolute position in mm and wait for completion."""
    counts = int(round(position_mm * COUNTS_PER_MM))
    data = struct.pack("<Hl", CHAN, counts)   # channel (short) + position (signed long)
    _write(dev, _long_msg(MGMSG_MOT_MOVE_ABSOLUTE, data))
    print(f"Moving to {position_mm:.3f} mm ({counts} counts)...")
    _wait_for(dev, MGMSG_MOT_MOVE_COMPLETED, timeout=30)
    pos = get_position(dev)
    print(f"Position: {pos:.4f} mm")
    return pos


def get_position(dev) -> float:
    """Return current position in mm."""
    _write(dev, _short_msg(MGMSG_MOT_REQ_POSCOUNTER, CHAN, 0))
    raw = _read(dev, 12, timeout=500)
    if len(raw) < 12:
        raise RuntimeError(f"Short position reply ({len(raw)} bytes)")
    counts = struct.unpack_from("<l", raw, 8)[0]
    return counts / COUNTS_PER_MM


def _wait_for(dev, target_id: int, timeout: float = 30):
    """Read packets until we see the target message ID."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = _read(dev, 64, timeout=500)
        if len(raw) < 6:
            continue
        # Scan through buffer for target message
        for i in range(len(raw) - 5):
            msg_id = struct.unpack_from("<H", raw, i)[0]
            if msg_id == target_id:
                return
    raise TimeoutError(f"Timed out waiting for message 0x{target_id:04X}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def demo(dev):
    info = get_info(dev)
    if info:
        print(f"Device: {info['model']}  serial={info['serial']}  fw={info['firmware']:08X}")
    home(dev)
    for target in [5.0, 10.0, 15.0, 10.0, 0.0]:
        move_to(dev, target)
        time.sleep(0.3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KDC101 / MTS25-Z8 stage control")
    parser.add_argument("--home",        action="store_true", help="Home the stage")
    parser.add_argument("--move",        type=float, metavar="MM", help="Move to position in mm")
    parser.add_argument("--pos",         action="store_true", help="Print current position")
    parser.add_argument("--demo",        action="store_true", help="Run movement demo")
    args = parser.parse_args()

    dev = _open_ftdi()
    print("Connected to KDC101.")

    if args.demo:
        demo(dev)
    else:
        if args.pos or not (args.home or args.move):
            print(f"Position: {get_position(dev):.4f} mm")
        if args.home:
            home(dev)
        if args.move is not None:
            move_to(dev, args.move)

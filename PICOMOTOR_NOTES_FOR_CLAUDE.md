# New Focus 8742 Picomotor Controller — Integration Notes

These notes are for a Claude instance integrating the 8742 into a new project.
Read this alongside the `8742-User-Manual.pdf`.

---

## Confirmed working environment

- macOS (Darwin), Python 3.14, pyusb 1.3.1, libusb 1.0.29 (via Homebrew)
- Controller: New Focus 8742, firmware v3.04, serial number **106326**
- One standard Picomotor on axis 1, confirmed moving

---

## Hardware / USB details

| Item | Value |
|---|---|
| USB Vendor ID | `0x104D` (Newport / New Focus) |
| USB Product ID | `0x4000` (8742 four-axis open-loop controller) |
| USB class | `0xFF` vendor-specific (NOT HID despite the marketing) |
| Endpoints | BULK OUT `0x02`, BULK IN `0x81`, 64-byte max packet |

---

## Critical protocol details (hard-won)

1. **Terminator is `\r` (carriage return), not `\n`.**
   The manual (section 5.3.1) incorrectly says `<LF>`. The device only responds
   to `\r`. This is the most important gotcha — using `\n` causes every read to
   time out silently.

2. **Do not specify a timeout on the bulk IN read.**
   Using an explicit timeout (e.g. 1000 ms) causes reads to time out on macOS
   even when the device has data ready. Use the default (no timeout argument).

3. **Send only the raw command bytes — no zero-padding.**
   Padding commands to 64 bytes causes the device to ignore them.

4. **On macOS, skip `is_kernel_driver_active()` — it raises `[Errno 2]`.**
   Guard it with a platform check and skip entirely on Darwin.

5. **Explicitly claim the USB interface before reading.**
   `usb.util.claim_interface(dev, 0)` is required on macOS for reads to work.

6. **Responses are terminated with `\r\n`.**
   Strip trailing whitespace and null bytes from all responses.

---

## Key commands

| Command | Example | Description |
|---|---|---|
| `*IDN?` | — | Full ID string; last token is serial number |
| `VE?` | — | Firmware version string |
| `MC` | — | **Motor Check** — physically pulses all axes to detect connected motors. Must run before `QM?` gives valid results. |
| `xxQM?` | `1QM?` | Motor type query (0=none, 1=unknown, 2=tiny, 3=standard). Reads memory only — run MC first. |
| `xxTP?` | `1TP?` | Get current step counter position |
| `xxDH` | `1DH` | Define home — zeros step counter at current position |
| `xxPR` | `1PR1000` | Relative move (steps) |
| `xxPA` | `1PA0` | Absolute move (steps, relative to home) |
| `xxMV+/-` | `1MV+` | Jog indefinitely |
| `xxST` | `1ST` | Stop axis (with deceleration) |
| `AB` | — | Abort — emergency stop all axes immediately |
| `xxMD?` | `1MD?` | Motion done? 0=moving, 1=stopped. Poll this after a move. |
| `TB?` | — | Get error message string |
| `SM` | — | Save current settings to non-volatile memory |
| `RS` | — | Soft reset the controller |

Axis numbers are 1–4. Commands are case-insensitive.
Multiple commands on one line are separated by `;`.

---

## Minimal working example (pyusb)

```python
import sys, time
import usb.core, usb.util

dev = usb.core.find(idVendor=0x104D, idProduct=0x4000)
dev.set_configuration()
cfg  = dev.get_active_configuration()
intf = cfg[(0, 0)]
usb.util.claim_interface(dev, intf.bInterfaceNumber)

ep_out = usb.util.find_descriptor(intf, custom_match=lambda e:
    usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
ep_in  = usb.util.find_descriptor(intf, custom_match=lambda e:
    usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)

def send(cmd):
    ep_out.write((cmd.strip() + "\r").encode("ascii"))

def recv():
    return "".join(chr(x) for x in bytes(ep_in.read(100))).rstrip()

def query(cmd):
    send(cmd)
    time.sleep(0.05)
    return recv()

# Identify
print(query("*IDN?"))

# Detect motors (MC physically pulses each axis)
send("MC")
time.sleep(3)
for ax in range(1, 5):
    print(f"Axis {ax} motor type:", query(f"{ax}QM?"))

# Move axis 1 by +1000 steps
send("1PR1000")
while query("1MD?").strip() != "1":
    time.sleep(0.05)
print("Position:", query("1TP?"))

usb.util.dispose_resources(dev)
```

---

## Motor detection gotcha

`QM?` reads a **cached value from memory**, not the live hardware state.
Always run `MC` after connecting or changing motors, otherwise `QM?` returns 0
(no motor) even when a motor is physically present.
After `MC` you can optionally save the result with `SM` so it persists across
power cycles.

---

## Dependencies

```bash
pip install pyusb
brew install libusb      # macOS — the C backend for pyusb
```

No other packages required. Does not use `hid`, `pyvisa`, or any Newport SDK.

# Laser Spectrometer

Python acquisition software for the RGB Photonics Qseries USB-C spectrometer (Qmini, Qmini2). Includes a headless single-shot script and a full PyQt6 GUI with real-time display, interval acquisition, and a time-domain pulse view.

## Features

- **Single-shot acquisition** — grab a spectrum and save as PNG
- **Free-run mode** — continuous acquisition at the fastest rate the exposure allows
- **Interval acquisition** — acquire at a fixed interval for a set duration or indefinitely, auto-saving CSV and/or PNG per spectrum
- **Time-domain view** — reconstruct the pulse envelope via FFT with zero-padding (~0.07 fs time resolution)
- **Save CSV / PNG** — manual save of the current spectrum at any time

## Requirements

- macOS (tested on macOS 13+, Apple Silicon)
- Python 3.10+
- [Homebrew](https://brew.sh)

---

## Installation

### 1. Install Homebrew (if not already installed)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. Install libusb

`pyusb` requires `libusb` to communicate with USB devices:

```bash
brew install libusb
```

### 3. Install Python 3 (if not already installed)

```bash
brew install python@3.13
```

### 4. Clone the repository

```bash
git clone git@github.com:MattBainLCLS/laser_spectrometer.git
cd laser_spectrometer
```

### 5. Create a virtual environment and install dependencies

```bash
python3 -m venv .env_spectrometer
source .env_spectrometer/bin/activate
pip install -r requirements.txt
```

### 6. Create the macOS Launchpad app (optional)

To launch the GUI directly from macOS Launchpad or Spotlight, run the included install script from the repository root:

```bash
bash install_app.sh
```

This will:
- Create the `.app` bundle in `/Applications/`
- Set the launcher path to your local clone automatically
- Generate the spectrum icon via `make_icon.py`
- Notify Launchpad so the app appears within a few seconds

The virtual environment (step 5) must be set up before running this.

---

### 7. Thorlabs KDC101 delay stage (optional)

The stage controller communicates directly via USB using the FTDI protocol — **no FTDI VCP driver is required**. You do need `libftdi` installed via Homebrew and a symlink so the Python bindings can find it.

#### a. Install libftdi

```bash
brew install libftdi
```

#### b. Create the library symlink

Homebrew installs `libftdi1.dylib` but the Python bindings look for `libftdi.dylib`:

```bash
ln -sf /opt/homebrew/lib/libftdi1.dylib /opt/homebrew/lib/libftdi.dylib
```

> **Intel Mac:** replace `/opt/homebrew` with `/usr/local` in the path above.

The Python packages (`pylablib`, `pylibftdi`, `pyserial`) are included in `requirements.txt` and installed in step 5.

#### c. Hardware setup

1. Connect the KDC101's power supply — USB alone is not enough to power it.
2. Connect the KDC101 to your Mac via USB (mini-USB port on the back of the controller).
3. Verify the Mac can see it:

```bash
python3 -c "import usb.core; d = usb.core.find(idVendor=0x0403, idProduct=0xfaf0); print('Found' if d else 'Not found')"
```

#### d. Run the stage GUI

```bash
source .env_spectrometer/bin/activate
python stage_gui.py
```

**Controls:**

| Control | Description |
|---|---|
| Position display | Live readout in mm; shows delay in fs once t0 is set |
| Home Stage | Sends the stage to its home position (run this first) |
| Jog ◀ / ▶ | Move relative to current position by the set step size |
| Go To Position | Move to an absolute position in mm |
| Set t0 | Marks the current position as time-zero for delay calculations |
| Delay Scan | Sweep over a range of delays (in fs) relative to t0 |

> **Note:** The stage must be homed at least once after power-on before absolute moves will be accurate.

---

### 8. Avantes spectrometer (optional)

The Avantes SDK wrapper (`Avantes/avaspec.py`) is included, but the compiled native library is **not** bundled for licensing reasons.

To use an Avantes device:

1. Download the Avantes SDK from [avantes.com/support/software](https://www.avantes.com/support/software/)
2. Copy the native library files into the `Avantes/` folder of this project:

   | Platform | Files to copy |
   |---|---|
   | macOS | `libavs.0.dylib` and `libavs.9.x.x.x.dylib` |
   | Linux | `libavs.so.0` |
   | Windows | `avaspecx64.dll` (64-bit) or `avaspec.dll` (32-bit) |

The library loader checks the `Avantes/` folder first, then falls back to `/usr/local/lib/`. If the library is missing a clear error message will be shown at startup.

---

## Usage

### GUI (recommended)

```bash
source .env_spectrometer/bin/activate
python spectrometer_gui.py
```

**Controls:**

| Control | Description |
|---|---|
| Exposure (s) | Integration time — adjust until Load level is ~0.6–0.8 |
| Grab Spectrum | Single acquisition |
| Start Free Run | Continuous acquisition until stopped |
| Show Time Domain | Opens the time-domain panel to the right |
| Save PNG | Save the current plot to `spectrum.png` |
| Save CSV | Save wavelength + intensity data with metadata header |
| Interval Acquisition | Acquire at a fixed interval, saving files automatically |

**Time Domain panel:**

| Control | Description |
|---|---|
| Time window ± | Sets the x-axis range in femtoseconds |
| Smoothing σ | Gaussian smoothing of the intensity spectrum before FFT (pixels) |
| dt label | Displays the actual time resolution of the current transform |

### Headless single-shot

```bash
source .env_spectrometer/bin/activate
python acquire_spectrum.py
```

Saves `spectrum.png` in the current directory.

---

## Troubleshooting

### Device not found

Make sure the spectrometer is connected and recognised by the OS:

```bash
system_profiler SPUSBDataType | grep -A5 "RGB Photonics"
```

### Permission denied (USB)

On macOS, USB access is managed by the OS and does not require udev rules (unlike Linux). If you see a permissions error, ensure no other application (e.g. a vendor GUI) has the device open.

### libusb not found

If `pyusb` cannot find `libusb` at runtime, reinstall via Homebrew and confirm the library is in a standard path:

```bash
brew reinstall libusb
ls /opt/homebrew/lib/libusb*   # Apple Silicon
ls /usr/local/lib/libusb*      # Intel Mac
```

### KDC101 stage not found

If the USB check above prints "Not found", confirm:
- The controller is powered on (green LED on front panel)
- The USB cable is plugged in to the mini-USB port on the **back** of the KDC101
- Run `system_profiler SPUSBDataType | grep -A5 "Thorlabs"` to confirm the Mac sees it at the USB level

If it appears in `system_profiler` but `pyusb` returns "Not found", check that `pyusb` and `libusb` are installed correctly (see the libusb step above).

### libftdi symlink missing

If you see `NameError: name 'ftdi' is not defined` when running the stage GUI, the symlink from step 7b is missing or points to the wrong place. Re-run:

```bash
ln -sf /opt/homebrew/lib/libftdi1.dylib /opt/homebrew/lib/libftdi.dylib
```

### Linux udev rules

If running on Linux, copy the provided udev rules to allow non-root USB access:

```bash
sudo cp NioLink/Python/pyrgbdriverkit-0.3.7/etc/51-rgbdevices.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

---

## SDK

The [RGB Photonics pyrgbdriverkit](https://www.rgb-photonics.eu) v0.3.7 is bundled in the `NioLink/` directory. The driver has been patched (`qseriesdriver.py`) to handle macOS USB behaviour correctly — `detach_kernel_driver` is skipped gracefully since macOS manages USB device access differently to Linux.

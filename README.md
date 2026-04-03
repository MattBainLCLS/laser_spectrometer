# Laser Spectrometer

Python acquisition software for the RGB Photonics Qseries USB-C spectrometer (Qmini, Qmini2) with optional Thorlabs KDC101 delay stage control. Includes a live spectrometer GUI, a stage controller GUI, and a combined delay-scan GUI with post-scan analysis.

## Features

- **Spectrometer GUI** — live spectrum display, free-run and single-shot acquisition, spectrum averaging, time-domain pulse view, reference pinning, interval acquisition with auto-save
- **Stage GUI** — position display, home, jog, go-to, t0 reference, and a stage-only delay scan
- **Scan GUI** — combined delay scan with per-step spectrum acquisition, live 2D waterfall plot, adjustable averaging and spectral window, post-scan Gaussian fit for pulse duration
- **Save CSV / PNG** — manual or automatic save at any time

---

## Requirements

- macOS (tested on macOS 13+, Apple Silicon and Intel)
- Python 3.10+
- [Homebrew](https://brew.sh)

---

## Installation

### 1. Install Homebrew (if not already installed)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. Install system libraries

```bash
brew install libusb libftdi
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

### 6. Create the libftdi symlink

Homebrew installs `libftdi1.dylib` but the Python bindings look for `libftdi.dylib`:

```bash
ln -sf /opt/homebrew/lib/libftdi1.dylib /opt/homebrew/lib/libftdi.dylib
```

> **Intel Mac:** replace `/opt/homebrew` with `/usr/local` in the path above.

### 7. Create the macOS Launchpad app (optional)

To launch the GUI directly from macOS Launchpad or Spotlight:

```bash
bash install_app.sh
```

This creates an `.app` bundle in `/Applications/`, generates the icon, and notifies Launchpad. The virtual environment (step 5) must be set up first.

---

## Hardware setup

### Spectrometer (RGB Photonics Qmini / Qmini2)

1. Connect the spectrometer via USB-C.
2. Verify the Mac sees it:

```bash
system_profiler SPUSBDataType | grep -A5 "RGB Photonics"
```

### KDC101 delay stage

1. Connect the KDC101's power supply — USB alone is not enough to power it.
2. Connect the KDC101 to your Mac via USB (mini-USB port on the back of the controller).
3. Verify the Mac sees it:

```bash
python3 -c "import usb.core; d = usb.core.find(idVendor=0x0403, idProduct=0xfaf0); print('Found' if d else 'Not found')"
```

> **Note:** The stage must be homed at least once after power-on before absolute moves will be accurate.

---

## Usage

### Scan GUI (stage + spectrometer together)

```bash
source .env_spectrometer/bin/activate
python scan_gui.py
```

This is the main acquisition interface. The window has three panels:

**Left — Stage controls**

| Control | Description |
|---|---|
| Position display | Live readout in mm; shows delay in fs once t0 is set |
| Home Stage | Sends the stage to its home position — run this first |
| Jog ◀ / ▶ | Move relative to current position by the set step size |
| Go To Position | Move to an absolute position in mm |
| Set t0 | Marks the current position as time-zero for delay calculations |

**Left — Scan parameters**

| Control | Description |
|---|---|
| Start / Stop / Step (fs) | Delay range and step size relative to t0 |
| λ range (nm) | Spectral window used for the scan and post-scan fit |
| ● indicator | Green = all positions within stage travel; red = some positions out of range |
| Start Scan / Abort | Run or stop the scan |
| Save Scan Data… | Save the full 2D dataset as a `.npz` file |

**Top right — Spectrometer**

| Control | Description |
|---|---|
| Exposure (s) | Integration time — aim for Load ~0.6–0.8 |
| Averages | Number of exposures averaged per scan step (and in free-run/grab) |
| Grab Spectrum | Single acquisition |
| Start Free Run | Continuous acquisition until stopped |
| Show Time Domain | Opens the time-domain (FFT) panel |
| Pin Reference / Clear | Overlay a reference spectrum on the plot |

**Bottom right — Live 2D scan plot**

Updates step-by-step as the scan runs. Colour axis rescales automatically.

**Post-scan analysis window**

Opens automatically when a scan completes. Integrates the spectrum over the selected λ range at each delay step and fits a Gaussian to extract the pulse duration (FWHM). Starting values are auto-guessed; adjust them and click **Fit Gaussian** if the automatic fit fails.

---

### Spectrometer GUI (spectrometer only)

```bash
source .env_spectrometer/bin/activate
python spectrometer_gui.py
```

| Control | Description |
|---|---|
| Exposure (s) | Integration time |
| Averages | Number of exposures to average per frame |
| Grab Spectrum | Single acquisition |
| Start Free Run | Continuous acquisition |
| Show Time Domain | FFT pulse envelope panel |
| Save PNG / CSV | Save current spectrum |
| Interval Acquisition | Acquire at a fixed interval, auto-saving files |

---

### Stage GUI (stage only)

```bash
source .env_spectrometer/bin/activate
python stage_gui.py
```

Includes position display, home, jog, go-to, t0, and a stage-only delay scan (no spectrum acquisition).

---

## Troubleshooting

### Spectrometer not found

```bash
system_profiler SPUSBDataType | grep -A5 "RGB Photonics"
```

If it appears there but the app can't connect, make sure no other application (e.g. the vendor GUI) has the device open.

### KDC101 not found

- Confirm the controller is powered on (green LED on front panel)
- Confirm the USB cable is in the mini-USB port on the **back** of the KDC101
- Run `system_profiler SPUSBDataType | grep -A5 "Thorlabs"` to confirm the Mac sees it at the USB level

If it appears in `system_profiler` but `pyusb` returns "Not found", check that `libusb` is installed (`brew install libusb`).

### libftdi symlink missing

If you see `NameError: name 'ftdi' is not defined` when running the stage GUI, the symlink from the installation step is missing. Re-run:

```bash
ln -sf /opt/homebrew/lib/libftdi1.dylib /opt/homebrew/lib/libftdi.dylib
```

### libusb not found

```bash
brew reinstall libusb
ls /opt/homebrew/lib/libusb*   # Apple Silicon
ls /usr/local/lib/libusb*      # Intel Mac
```

### Linux udev rules

If running on Linux, copy the provided udev rules to allow non-root USB access:

```bash
sudo cp NioLink/Python/pyrgbdriverkit-0.3.7/etc/51-rgbdevices.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

---

## Avantes spectrometer (optional)

The Avantes SDK wrapper (`Avantes/avaspec.py`) is included, but the compiled native library is **not** bundled for licensing reasons.

1. Download the Avantes SDK from [avantes.com/support/software](https://www.avantes.com/support/software/)
2. Copy the native library into the `Avantes/` folder:

| Platform | File |
|---|---|
| macOS | `libavs.0.dylib` and `libavs.9.x.x.x.dylib` |
| Linux | `libavs.so.0` |
| Windows | `avaspecx64.dll` (64-bit) or `avaspec.dll` (32-bit) |

---

## SDK notes

The [RGB Photonics pyrgbdriverkit](https://www.rgb-photonics.eu) v0.3.7 is bundled in `NioLink/`. The driver has been patched (`qseriesdriver.py`) to handle macOS USB behaviour correctly — `detach_kernel_driver` is skipped gracefully since macOS manages USB device access differently to Linux.

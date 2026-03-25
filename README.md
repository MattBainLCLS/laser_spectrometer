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

### 6. Avantes spectrometer (optional)

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

### Linux udev rules

If running on Linux, copy the provided udev rules to allow non-root USB access:

```bash
sudo cp NioLink/Python/pyrgbdriverkit-0.3.7/etc/51-rgbdevices.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

---

## SDK

The [RGB Photonics pyrgbdriverkit](https://www.rgb-photonics.eu) v0.3.7 is bundled in the `NioLink/` directory. The driver has been patched (`qseriesdriver.py`) to handle macOS USB behaviour correctly — `detach_kernel_driver` is skipped gracefully since macOS manages USB device access differently to Linux.

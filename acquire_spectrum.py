import sys
import time
import matplotlib.pyplot as plt

sys.path.append("NioLink/Python/pyrgbdriverkit-0.3.7")

from rgbdriverkit.qseriesdriver import Qseries
from rgbdriverkit.calibratedspectrometer import SpectrometerProcessing

def main():
    devices = Qseries.search_devices()
    if not devices:
        sys.exit("No spectrometer found.")

    spec = Qseries(devices[0])
    spec.open()

    print(f"Connected: {spec.model_name} | S/N: {spec.serial_number} | FW: {spec.software_version}")

    wavelengths = spec.get_wavelengths()

    spec.exposure_time = 0.1  # seconds — adjust as needed
    spec.processing_steps = SpectrometerProcessing.AdjustOffset
    spec.start_exposure(1)

    while not spec.available_spectra:
        time.sleep(0.05)

    data = spec.get_spectrum_data()
    spec.close()

    print(f"Spectrum acquired | Load level: {data.LoadLevel:.2f} | Exposure: {data.ExposureTime}s")
    if data.LoadLevel > 1:
        print("Warning: sensor overloaded — reduce exposure_time")

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(wavelengths, data.Spectrum)
    ax.set_xlabel("Wavelength (nm)")
    ax.set_ylabel("Intensity (ADC counts)")
    ax.set_title(f"Spectrum — {spec.model_name} | {data.TimeStamp}")
    ax.grid(True)
    plt.tight_layout()
    plt.savefig("spectrum.png", dpi=150)
    print("Saved spectrum.png")

if __name__ == "__main__":
    main()

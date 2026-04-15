import os
import sys
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor", "Avantes"))
import time
import numpy as np
import matplotlib.pyplot as plt
from avaspec import (
    AVS_Init, AVS_GetList, AVS_Activate, AVS_Deactivate, AVS_Done,
    AVS_GetNumPixels, AVS_GetLambda, AVS_GetParameter,
    AVS_PrepareMeasure, AVS_Measure, AVS_PollScan, AVS_GetScopeData,
    AVS_GetVersionInfo, MeasConfigType,
)

def main():
    # --- Connect ---
    n = AVS_Init(0)
    print(f"AVS_Init: {n} device(s) found")
    if n == 0:
        sys.exit("No Avantes spectrometer found.")

    devices = AVS_GetList(n)
    dev = devices[0]
    print(f"Device:  {dev.UserFriendlyName.decode().strip()}")
    print(f"Serial:  {dev.SerialNumber.decode().strip()}")

    handle = AVS_Activate(dev)
    if handle == 1000:  # INVALID_AVS_HANDLE_VALUE
        sys.exit("Failed to activate device.")

    fpga, fw, dll = AVS_GetVersionInfo(handle)
    print(f"FW: {fw.value.decode().strip()}  FPGA: {fpga.value.decode().strip()}  DLL: {dll.value.decode().strip()}")

    num_pixels = AVS_GetNumPixels(handle)
    print(f"Pixels:  {num_pixels}")

    wavelengths = list(AVS_GetLambda(handle))[:num_pixels]

    # --- Configure ---
    cfg = MeasConfigType()
    cfg.m_StartPixel       = 0
    cfg.m_StopPixel        = num_pixels - 1
    cfg.m_IntegrationTime  = 0.1   # seconds — adjust if overloaded
    cfg.m_NrAverages       = 1
    cfg.m_SaturationDetection = 1

    ret = AVS_PrepareMeasure(handle, cfg)
    if ret != 0:
        sys.exit(f"AVS_PrepareMeasure failed: {ret}")

    # --- Acquire ---
    AVS_Measure(handle, 0, 1)
    print("Waiting for spectrum...")
    while not AVS_PollScan(handle):
        time.sleep(0.01)

    timestamp, raw = AVS_GetScopeData(handle)
    spectrum = np.array(list(raw)[:num_pixels])

    load = spectrum.max() / 65535.0
    print(f"Acquired  |  max counts: {spectrum.max():.0f}  |  load: {load:.2f}")
    if load > 1.0:
        print("Warning: sensor overloaded — reduce integration time")

    # --- Disconnect ---
    AVS_Deactivate(handle)
    AVS_Done()

    # --- Plot ---
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(wavelengths, spectrum)
    ax.set_xlabel("Wavelength (nm)")
    ax.set_ylabel("Intensity (counts)")
    ax.set_title(f"Avantes {dev.UserFriendlyName.decode().strip()} | S/N {dev.SerialNumber.decode().strip()}")
    ax.grid(True)
    plt.tight_layout()
    plt.savefig("spectrum_avantes.png", dpi=150)
    print("Saved spectrum_avantes.png")

if __name__ == "__main__":
    main()

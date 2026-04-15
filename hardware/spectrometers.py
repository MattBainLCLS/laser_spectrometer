"""
Hardware abstraction layer for spectrometers.

Provides a unified interface over RGB Photonics Qseries and Avantes devices.
Add new manufacturers by subclassing SpectrometerBase and registering a
discovery call in find_spectrometer().
"""

import os
import sys
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")


# ---------------------------------------------------------------------------
# Common data type returned by every adapter
# ---------------------------------------------------------------------------

@dataclass
class SpectrumResult:
    spectrum:      np.ndarray
    timestamp:     datetime
    exposure_time: float              # seconds
    load_level:    float              # 0–1 normal, >1 overloaded
    averaging:     int = 1
    std:           np.ndarray | None = None   # sample std across averages


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class SpectrometerBase(ABC):

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    @abstractmethod
    def serial_number(self) -> str: ...

    @property
    @abstractmethod
    def firmware_version(self) -> str: ...

    @property
    @abstractmethod
    def min_exposure_time(self) -> float: ...

    @property
    @abstractmethod
    def max_exposure_time(self) -> float: ...

    @property
    @abstractmethod
    def exposure_time(self) -> float: ...

    @exposure_time.setter
    @abstractmethod
    def exposure_time(self, value: float): ...

    @abstractmethod
    def open(self): ...

    @abstractmethod
    def close(self): ...

    @abstractmethod
    def get_wavelengths(self) -> list: ...

    @abstractmethod
    def start_exposure(self): ...

    @abstractmethod
    def is_data_ready(self) -> bool: ...

    @abstractmethod
    def get_spectrum(self) -> SpectrumResult: ...


# ---------------------------------------------------------------------------
# RGB Photonics Qseries adapter
# ---------------------------------------------------------------------------

class QseriesAdapter(SpectrometerBase):

    def __init__(self, device):
        sys.path.append(os.path.join(_VENDOR, "NioLink", "Python", "pyrgbdriverkit-0.3.7"))
        from rgbdriverkit.qseriesdriver import Qseries
        from rgbdriverkit.calibratedspectrometer import SpectrometerProcessing
        self._spec = Qseries(device)
        self._Processing = SpectrometerProcessing

    @property
    def model_name(self):       return self._spec.model_name
    @property
    def serial_number(self):    return self._spec.serial_number
    @property
    def firmware_version(self): return self._spec.software_version
    @property
    def min_exposure_time(self): return self._spec.min_exposure_time
    @property
    def max_exposure_time(self): return self._spec.max_exposure_time
    @property
    def exposure_time(self):    return self._spec.exposure_time

    @exposure_time.setter
    def exposure_time(self, value):
        self._spec.exposure_time = value

    def open(self):
        self._spec.open()
        self._spec.processing_steps = self._Processing.AdjustOffset

    def close(self):
        self._spec.close()

    def get_wavelengths(self):
        return self._spec.get_wavelengths()

    def start_exposure(self):
        self._spec.start_exposure(1)

    def is_data_ready(self):
        return bool(self._spec.available_spectra)

    def get_spectrum(self) -> SpectrumResult:
        d = self._spec.get_spectrum_data()
        return SpectrumResult(
            spectrum=np.asarray(d.Spectrum),
            timestamp=d.TimeStamp,
            exposure_time=d.ExposureTime,
            load_level=d.LoadLevel,
            averaging=d.Averaging,
        )


# ---------------------------------------------------------------------------
# Avantes adapter
# ---------------------------------------------------------------------------

class AvantesAdapter(SpectrometerBase):

    def __init__(self, identity):
        sys.path.append(os.path.join(_VENDOR, "Avantes"))
        from avaspec import (
            AVS_Activate, AVS_Deactivate, AVS_Done, AVS_GetVersionInfo,
            AVS_GetNumPixels, AVS_GetLambda,
            AVS_PrepareMeasure, AVS_Measure, AVS_PollScan, AVS_GetScopeData,
            MeasConfigType,
        )
        self._identity = identity
        self._handle = None
        self._num_pixels = 0
        self._exposure = 0.1
        self._fw_version = "–"

        self._AVS_Activate       = AVS_Activate
        self._AVS_Deactivate     = AVS_Deactivate
        self._AVS_Done           = AVS_Done
        self._AVS_GetVersionInfo = AVS_GetVersionInfo
        self._AVS_GetNumPixels   = AVS_GetNumPixels
        self._AVS_GetLambda      = AVS_GetLambda
        self._AVS_PrepareMeasure = AVS_PrepareMeasure
        self._AVS_Measure        = AVS_Measure
        self._AVS_PollScan       = AVS_PollScan
        self._AVS_GetScopeData   = AVS_GetScopeData
        self._MeasConfigType     = MeasConfigType

    @property
    def model_name(self):
        return self._identity.UserFriendlyName.decode().strip()

    @property
    def serial_number(self):
        return self._identity.SerialNumber.decode().strip()

    @property
    def firmware_version(self):
        return self._fw_version

    @property
    def min_exposure_time(self): return 0.001   # 1 ms

    @property
    def max_exposure_time(self): return 60.0

    @property
    def exposure_time(self): return self._exposure

    @exposure_time.setter
    def exposure_time(self, value):
        self._exposure = value

    def open(self):
        self._handle = self._AVS_Activate(self._identity)
        if self._handle == 1000:
            raise IOError("Failed to activate Avantes device.")
        _, fw, _ = self._AVS_GetVersionInfo(self._handle)
        self._fw_version = fw.value.decode().strip()
        self._num_pixels = self._AVS_GetNumPixels(self._handle)

    def close(self):
        if self._handle is not None:
            self._AVS_Deactivate(self._handle)
            self._handle = None
        self._AVS_Done()

    def _build_config(self):
        cfg = self._MeasConfigType()
        cfg.m_StartPixel      = 0
        cfg.m_StopPixel       = self._num_pixels - 1
        cfg.m_IntegrationTime = self._exposure
        cfg.m_NrAverages      = 1
        cfg.m_SaturationDetection = 1
        return cfg

    def get_wavelengths(self):
        return list(self._AVS_GetLambda(self._handle))[:self._num_pixels]

    def start_exposure(self):
        self._AVS_PrepareMeasure(self._handle, self._build_config())
        self._AVS_Measure(self._handle, 0, 1)

    def is_data_ready(self):
        return bool(self._AVS_PollScan(self._handle))

    def get_spectrum(self) -> SpectrumResult:
        _, raw = self._AVS_GetScopeData(self._handle)
        spectrum = np.frombuffer(raw, dtype=np.float64)[:self._num_pixels].copy()
        return SpectrumResult(
            spectrum=spectrum,
            timestamp=datetime.now(),
            exposure_time=self._exposure,
            load_level=float(spectrum.max()) / 65535.0,
            averaging=1,
        )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def find_spectrometer() -> SpectrometerBase | None:
    """Try each known SDK in turn; return an adapter for the first device found."""

    # RGB Photonics Qseries
    try:
        sys.path.append(os.path.join(_VENDOR, "NioLink", "Python", "pyrgbdriverkit-0.3.7"))
        from rgbdriverkit.qseriesdriver import Qseries
        devices = Qseries.search_devices()
        if devices:
            return QseriesAdapter(devices[0])
    except Exception:
        pass

    # Avantes — AVS_Init is called here; the adapter takes ownership
    try:
        sys.path.append(os.path.join(_VENDOR, "Avantes"))
        from avaspec import AVS_Init, AVS_GetList, AVS_Done
        n = AVS_Init(0)
        if n > 0:
            devices = AVS_GetList(n)
            return AvantesAdapter(devices[0])
        AVS_Done()
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Rolling acquisition buffer
# ---------------------------------------------------------------------------

class RollingBuffer:
    """
    Rolling FIFO acquisition buffer for a SpectrometerBase device.

    Wraps the single-frame acquire loop and maintains a deque of the last
    *n* SpectrumResult frames.  Used by both the live display (one frame at
    a time → fast rolling mean) and the FROG scan worker (flush + fill N
    fresh frames after each stage move).

    Parameters
    ----------
    spec : SpectrometerBase
        An open spectrometer instance.
    n : int
        Buffer depth — number of frames to average.
    """

    def __init__(self, spec: SpectrometerBase, n: int):
        self._spec   = spec
        self._n      = n
        self._buffer: deque = deque(maxlen=n)

    # ── Acquisition ───────────────────────────────────────────────────────────

    def acquire_one(self, stop_fn=None) -> bool:
        """
        Acquire one frame and append it to the buffer.

        Parameters
        ----------
        stop_fn : callable() → bool, optional
            Polled between is_data_ready() checks.  Return True to abort.

        Returns
        -------
        bool : True on success, False if *stop_fn* aborted the acquisition.
        """
        self._spec.start_exposure()
        while not self._spec.is_data_ready():
            if stop_fn is not None and stop_fn():
                return False
            time.sleep(0.01)
        self._buffer.append(self._spec.get_spectrum())
        return True

    def flush_and_fill(self, stop_fn=None) -> bool:
        """
        Discard all buffered frames then collect *n* fresh ones.

        Guarantees the result contains only frames acquired after this call
        returns — use after a stage move to avoid stale data in the mean.
        Returns False if *stop_fn* aborted before the buffer was full.
        """
        self._buffer.clear()
        for _ in range(self._n):
            if not self.acquire_one(stop_fn):
                return False
        return True

    # ── Results ───────────────────────────────────────────────────────────────

    def mean(self) -> SpectrumResult:
        """Mean SpectrumResult over the current buffer contents."""
        frames = list(self._buffer)
        arr    = np.array([f.spectrum for f in frames])
        last   = frames[-1]
        return SpectrumResult(
            spectrum      = np.mean(arr, axis=0),
            timestamp     = last.timestamp,
            exposure_time = last.exposure_time,
            load_level    = max(f.load_level for f in frames),
            averaging     = len(frames),
        )

    def std(self) -> np.ndarray:
        """
        Sample std (ddof=1) of spectra in the buffer.
        Returns zeros when the buffer holds fewer than 2 frames.
        """
        frames = list(self._buffer)
        if len(frames) < 2:
            return np.zeros_like(frames[0].spectrum) if frames else np.array([])
        return np.std([f.spectrum for f in frames], axis=0, ddof=1)

    def __len__(self) -> int:
        return len(self._buffer)

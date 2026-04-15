"""
Persistent configuration for picomotor mirror and stage assignments.

Config file location: ~/.config/spectrometer/picomotors.json

Each entry maps a (controller_serial, axis) pair to either a mirror role
(horizontal/vertical beam steering) or a stage role (linear delay line).

Typical JSON structure
----------------------
{
  "mirrors": [
    {
      "name": "Input coupler",
      "horizontal": {"controller": "106326", "axis": 1},
      "vertical":   {"controller": "106326", "axis": 2}
    }
  ],
  "stages": [
    {
      "name": "Picomotor delay line",
      "controller": "106326",
      "axis": 3,
      "steps_per_mm": 2000,
      "min_mm": -12.5,
      "max_mm": 12.5
    }
  ]
}
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


# ── Config file path ──────────────────────────────────────────────────────────

def config_path() -> Path:
    return Path.home() / ".config" / "spectrometer" / "picomotors.json"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class AxisRef:
    """Reference to one axis on one controller."""
    controller: str   # serial number string, e.g. "106326"
    axis: int         # 1–4


@dataclass
class MirrorConfig:
    name: str
    horizontal: AxisRef
    vertical: AxisRef


@dataclass
class StageConfig:
    name: str
    controller: str
    axis: int
    steps_per_mm: float
    min_mm: float
    max_mm: float


@dataclass
class PicomotorConfig:
    mirrors: list[MirrorConfig] = field(default_factory=list)
    stages:  list[StageConfig]  = field(default_factory=list)


# ── Load / save ───────────────────────────────────────────────────────────────

def load_config() -> PicomotorConfig:
    """
    Load config from disk.  Returns an empty PicomotorConfig if the file does
    not exist or cannot be parsed.
    """
    path = config_path()
    if not path.exists():
        return PicomotorConfig()

    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return PicomotorConfig()

    mirrors = []
    for m in data.get("mirrors", []):
        try:
            mirrors.append(MirrorConfig(
                name       = m["name"],
                horizontal = AxisRef(m["horizontal"]["controller"],
                                     int(m["horizontal"]["axis"])),
                vertical   = AxisRef(m["vertical"]["controller"],
                                     int(m["vertical"]["axis"])),
            ))
        except (KeyError, TypeError, ValueError):
            continue

    stages = []
    for s in data.get("stages", []):
        try:
            stages.append(StageConfig(
                name         = s["name"],
                controller   = s["controller"],
                axis         = int(s["axis"]),
                steps_per_mm = float(s["steps_per_mm"]),
                min_mm       = float(s["min_mm"]),
                max_mm       = float(s["max_mm"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue

    return PicomotorConfig(mirrors=mirrors, stages=stages)


def save_config(cfg: PicomotorConfig):
    """Write config to disk, creating parent directories as needed."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "mirrors": [
            {
                "name":       m.name,
                "horizontal": {"controller": m.horizontal.controller,
                               "axis":       m.horizontal.axis},
                "vertical":   {"controller": m.vertical.controller,
                               "axis":       m.vertical.axis},
            }
            for m in cfg.mirrors
        ],
        "stages": [
            {
                "name":         s.name,
                "controller":   s.controller,
                "axis":         s.axis,
                "steps_per_mm": s.steps_per_mm,
                "min_mm":       s.min_mm,
                "max_mm":       s.max_mm,
            }
            for s in cfg.stages
        ],
    }

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ── First-run template generation ────────────────────────────────────────────

def write_template(connected_serials: list[str]):
    """
    Write a pre-filled config template to disk using real serial numbers.

    Each controller gets two mirror slots (axes 1+2, 3+4) as a sensible
    default.  The user edits names and reassigns axes as needed.

    Parameters
    ----------
    connected_serials : list[str]
        Serial numbers of all 8742 controllers currently detected on USB,
        in discovery order.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    mirrors = []
    stages  = []

    for i, serial in enumerate(connected_serials):
        base = i * 2   # mirror index offset per controller
        mirrors.append({
            "name":       f"Mirror {base + 1}",
            "horizontal": {"controller": serial, "axis": 1},
            "vertical":   {"controller": serial, "axis": 2},
        })
        mirrors.append({
            "name":       f"Mirror {base + 2}",
            "horizontal": {"controller": serial, "axis": 3},
            "vertical":   {"controller": serial, "axis": 4},
        })

    # Commented-out stage example — not written as a real entry since
    # steps_per_mm is actuator-specific and must be set by the user.
    comment_stage = {
        "_comment":     "Uncomment and fill in to use a picomotor as a delay stage",
        "name":         "Picomotor delay line",
        "controller":   connected_serials[0] if connected_serials else "SERIAL",
        "axis":         1,
        "steps_per_mm": 2000,
        "min_mm":       -12.5,
        "max_mm":       12.5,
    }

    data = {
        "mirrors": mirrors,
        "stages":  [],
        "_stage_example": comment_stage,
    }

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def open_in_editor():
    """Open the config file in the system default text editor."""
    import subprocess
    path = config_path()
    if os.name == "nt":
        os.startfile(str(path))
    elif os.uname().sysname == "Darwin":
        subprocess.run(["open", str(path)])
    else:
        subprocess.run(["xdg-open", str(path)])


# ── Lookup helpers ────────────────────────────────────────────────────────────

def assigned_axes(cfg: PicomotorConfig) -> set[tuple[str, int]]:
    """Return all (controller_serial, axis) pairs referenced in the config."""
    axes: set[tuple[str, int]] = set()
    for m in cfg.mirrors:
        axes.add((m.horizontal.controller, m.horizontal.axis))
        axes.add((m.vertical.controller,   m.vertical.axis))
    for s in cfg.stages:
        axes.add((s.controller, s.axis))
    return axes


def unassigned_axes(cfg: PicomotorConfig,
                    available: dict[str, list[int]]) -> list[tuple[str, int]]:
    """
    Return (serial, axis) pairs that are physically present but not in config.

    Parameters
    ----------
    available : dict mapping controller serial → list of axes with motors
                (i.e. motor_type > MOTOR_NONE).
    """
    used = assigned_axes(cfg)
    unassigned = []
    for serial, axes in available.items():
        for axis in axes:
            if (serial, axis) not in used:
                unassigned.append((serial, axis))
    return unassigned

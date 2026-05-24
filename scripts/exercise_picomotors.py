"""
Quick exercise script for two daisy-chained 8742 controllers.

Topology:
  106326 (address 1) — master, USB to computer, XYZ stages on axes 1/2/3
  106323 (address 2) — slave, RS-485 only, mirror pair on axes 1/2

Prerequisite: run scripts/set_rs485_addresses.py once to assign unique
RS-485 addresses, then connect RS-485 cable and USB only to the master.

Each connected axis gets a +200 / -200 step round-trip.
Disconnected axes (motor type == MOTOR_NONE) are skipped.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hardware.picomotor import Picomotor8742, Picomotor8742Slave, MOTOR_NONE, _MOTOR_NAMES

STEPS = 200


def exercise(ctrl, label: str, axes: dict[int, str]):
    print(f"\n{'='*60}")
    print(f"  {label}  (serial {ctrl.serial_number})")
    print(f"{'='*60}")

    for axis, name in axes.items():
        mtype = ctrl.motor_type(axis)
        mname = _MOTOR_NAMES.get(mtype, "?")
        if mtype == MOTOR_NONE:
            print(f"  Axis {axis} ({name}): no motor detected — skipping")
            continue

        pos_before = ctrl.get_position(axis)
        print(f"  Axis {axis} ({name}): motor={mname}, pos={pos_before}")

        print(f"    → +{STEPS} steps …", end=" ", flush=True)
        ctrl.move_relative(axis, +STEPS)
        pos_mid = ctrl.get_position(axis)
        print(f"pos={pos_mid}")

        print(f"    → -{STEPS} steps …", end=" ", flush=True)
        ctrl.move_relative(axis, -STEPS)
        pos_after = ctrl.get_position(axis)
        net = pos_after - pos_before
        status = "OK" if abs(net) <= 2 else f"WARNING net drift={net} steps"
        print(f"pos={pos_after}  ({status})")


def main():
    print("Connecting to master (106326) via USB …", flush=True)
    master = Picomotor8742(serial="106326")
    master.connect()
    print(f"  Master connected: serial={master.serial_number}")

    print("Connecting to slave (106323) via RS-485 …", flush=True)
    slave = Picomotor8742Slave(master=master, rs485_address=2, serial="106323")
    slave.connect()
    print(f"  Slave connected:  serial={slave.serial_number}")

    try:
        exercise(master, "XYZ (master, direct USB)", {1: "X", 2: "Y", 3: "Z"})
        exercise(slave,  "Mirror pair (slave, RS-485)", {1: "H", 2: "V"})
    finally:
        master.close()

    print("\nAll axes exercised.")


if __name__ == "__main__":
    main()

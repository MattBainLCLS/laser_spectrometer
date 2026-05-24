"""
One-time RS-485 address setup for daisy-chained 8742 controllers.

Run this BEFORE connecting the RS-485 cable between the two controllers.
Connect each controller INDIVIDUALLY via USB when prompted.

Target layout:
  106326 → address 1  (master — stays at factory default, no change needed)
  106323 → address 2  (slave)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hardware.picomotor import Picomotor8742

ASSIGNMENTS = [
    {"serial": "106326", "address": 1, "role": "master"},
    {"serial": "106323", "address": 2, "role": "slave"},
]

print("RS-485 address setup")
print("=" * 50)
print("Connect each controller INDIVIDUALLY via USB when prompted.")
print("Disconnect the RS-485 cable between them for now.")
print()

for cfg in ASSIGNMENTS:
    serial  = cfg["serial"]
    address = cfg["address"]
    role    = cfg["role"]

    input(f"Connect ONLY controller {serial} ({role}) via USB, then press Enter...")

    ctrl = Picomotor8742(serial=serial)
    try:
        ctrl.connect(run_motor_check=False)
        current = ctrl.rs485_address()
        print(f"  {serial}: current RS-485 address = {current}")

        if current == address:
            print(f"  Already at address {address} — no change needed.")
        else:
            ctrl.set_rs485_address(address, save=True)
            verify = ctrl.rs485_address()
            print(f"  Set to {address} and saved to flash. Verified: SA? = {verify}")
    except Exception as exc:
        print(f"  ERROR: {exc}")
    finally:
        ctrl.close()

    print()

print("Setup complete.")
print()
print("Next steps:")
print("  1. Connect the RS-485 cable between the two controllers.")
print("  2. Connect USB only to the master (106326).")
print("  3. Run scripts/exercise_picomotors.py")

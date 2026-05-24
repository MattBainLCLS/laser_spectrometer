"""
CLI for the Thorlabs KDC101 / MTS25-Z8 stage.

Usage:
    python3 stage.py --home
    python3 stage.py --move 10.5
    python3 stage.py --pos
    python3 stage.py --demo
"""

import argparse
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time

from hardware.stages import find_stage, KDC101Stage


def demo(stage: KDC101Stage):
    info = stage.get_info()
    if info:
        print(f"Device: {info['model']}  serial={info['serial']}  "
              f"fw={info['firmware']:08X}")
    print("Homing...")
    stage.home()
    print("Homed.")
    for target in [5.0, 10.0, 15.0, 10.0, 0.0]:
        print(f"Moving to {target} mm...")
        stage.move_to(target)
        print(f"Position: {stage.get_position():.4f} mm")
        time.sleep(0.3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KDC101 / MTS25-Z8 stage control")
    parser.add_argument("--home", action="store_true", help="Home the stage")
    parser.add_argument("--move", type=float, metavar="MM",
                        help="Move to position in mm")
    parser.add_argument("--pos",  action="store_true",
                        help="Print current position")
    parser.add_argument("--demo", action="store_true",
                        help="Run movement demo")
    args = parser.parse_args()

    stage = find_stage()
    if stage is None:
        print("KDC101 not found — is it plugged in and powered on?")
        raise SystemExit(1)

    print(f"Connected: {stage.model_name}")

    if args.demo:
        demo(stage)
    else:
        if args.pos or not (args.home or args.move):
            print(f"Position: {stage.get_position():.4f} mm")
        if args.home:
            print("Homing...")
            stage.home()
            print("Homed.")
        if args.move is not None:
            print(f"Moving to {args.move} mm...")
            stage.move_to(args.move)
            print(f"Position: {stage.get_position():.4f} mm")

import argparse
import json
import sys
from typing import Dict, List, Optional, Set, Tuple
from mock.biosignal_mock import BiosignalGenerator


def verify_export(
    export_filepath: str,
    mode: str = "ramp",
    seed: int = 7,
    max_duration: int = 3600,
) -> Tuple[bool, List[str]]:
    generator = BiosignalGenerator(mode=mode, seed=seed, duration=max_duration)
    
    samples_found: Dict[int, Dict[int, List[int]]] = {1: {}, 2: {}}
    losses_reported: Dict[int, List[Tuple[int, Optional[int], str]]] = {1: [], 2: []}
    duplicates: List[str] = []
    corrupted: List[str] = []

    with open(export_filepath, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception as e:
                corrupted.append(f"Line {line_num}: invalid JSON: {e}")
                continue

            if "values" in record:
                # Sample record
                sensor = int(record["sensor"])
                idx = int(record["index"])
                values = record["values"]

                if idx in samples_found[sensor]:
                    duplicates.append(f"Sensor {sensor} duplicate index {idx}")
                samples_found[sensor][idx] = values

                # Validate values if ramp mode
                if mode == "ramp":
                    expected = generator.generate_sample(sensor, idx)
                    if values != expected:
                        corrupted.append(
                            f"Sensor {sensor} index {idx} value mismatch: got {values}, expected {expected}"
                        )

            elif "from_index" in record:
                # Loss record
                sensor = int(record["sensor"])
                f_idx = int(record["from_index"])
                t_idx = int(record["to_index"]) if record.get("to_index") is not None else None
                reason = str(record.get("reason", "unknown"))
                losses_reported[sensor].append((f_idx, t_idx, reason))

    errors = []
    if duplicates:
        errors.append(f"Found {len(duplicates)} duplicate samples (e.g. {duplicates[:3]})")
    if corrupted:
        errors.append(f"Found {len(corrupted)} corrupted values (e.g. {corrupted[:3]})")

    # Check for silent loss:
    for sensor in (1, 2):
        if not samples_found[sensor]:
            continue
        max_idx = max(samples_found[sensor].keys())
        min_idx = min(samples_found[sensor].keys())

        # Build coverage set of losses
        lost_indices: Set[int] = set()
        for f_idx, t_idx, _ in losses_reported[sensor]:
            if t_idx is not None:
                lost_indices.update(range(f_idx, t_idx + 1))
            else:
                # Open-ended loss (e.g. mock restart)
                lost_indices.update(range(f_idx, max_idx + 1))

        silent_losses = []
        for i in range(min_idx, max_idx + 1):
            if i not in samples_found[sensor] and i not in lost_indices:
                silent_losses.append(i)

        if silent_losses:
            errors.append(
                f"CRITICAL: Sensor {sensor} has {len(silent_losses)} SILENT LOSSES! (e.g. indices {silent_losses[:5]})"
            )

    is_valid = len(errors) == 0
    return is_valid, errors


def main():
    parser = argparse.ArgumentParser(description="Check pipeline export against ground truth")
    parser.add_argument("export_file", help="Path to JSON lines export file")
    parser.add_argument("--mode", choices=["ramp", "signal"], default="ramp", help="Mock data mode")
    parser.add_argument("--seed", type=int, default=7, help="Random seed")
    parser.add_argument("--duration", type=int, default=3600, help="Duration")
    args = parser.parse_args()

    print(f"Checking {args.export_file} against ground truth (mode={args.mode}, seed={args.seed})...")
    is_valid, errors = verify_export(
        args.export_file, mode=args.mode, seed=args.seed, max_duration=args.duration
    )
    if is_valid:
        print("PASS: 100% data integrity verified. No silent losses, no duplicates, all values match.")
        sys.exit(0)
    else:
        print("FAIL: Errors detected:")
        for err in errors:
            print(" -", err)
        sys.exit(1)


if __name__ == "__main__":
    main()

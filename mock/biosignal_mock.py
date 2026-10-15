import argparse
import asyncio
import json
import math
import random
import sys
import time
import uuid
import zlib
from typing import Any, Dict, List, Optional, Tuple


class BiosignalGenerator:
    def __init__(self, mode: str = "signal", seed: int = 7, duration: int = 3600):
        self.mode = mode
        self.seed = seed
        self.duration = duration
        self.rng = random.Random(seed)

    def generate_sample(self, sensor: int, index: int) -> List[int]:
        if self.mode == "ramp":
            if sensor == 1:
                return [(3 * index + c) % 8388608 for c in range(1, 4)]
            else:
                return [(2 * index + c) % 8388608 for c in range(1, 3)]

        # Signal mode: realistic synthetic data
        t_sec = index / 400.0
        cycle_min = (t_sec / 60.0) % 6.0

        if cycle_min < 2.0:
            target_bpm = 60.0
        elif cycle_min < 4.0:
            target_bpm = 90.0
        else:
            target_bpm = 72.0

        samples_per_beat = (60.0 / target_bpm) * 400.0
        phase = (index % samples_per_beat) / samples_per_beat

        # Base baseline with slight respiratory sinus arrhythmia
        baseline = 1000 + int(50 * math.sin(2 * math.pi * t_sec * 0.25))

        # Sharp ECG QRS-like peak when phase is near 0.1
        if 0.08 <= phase <= 0.12:
            peak_val = int(3000 * math.exp(-(((phase - 0.10) / 0.008) ** 2)))
        elif 0.18 <= phase <= 0.28:
            # T wave
            peak_val = int(400 * math.exp(-(((phase - 0.23) / 0.03) ** 2)))
        else:
            peak_val = 0

        ch2 = baseline + peak_val

        if sensor == 1:
            ch1 = int(ch2 * 0.6)
            ch3 = int(ch2 * 1.3)
            return [ch1, ch2, ch3]
        else:
            # Sensor 2: channel 1 near 400,000, channel 2 near 150,000 varying slowly
            s2_ch1 = int(400000 + 5000 * math.sin(2 * math.pi * t_sec * 0.05))
            s2_ch2 = int(150000 + 3000 * math.cos(2 * math.pi * t_sec * 0.04))
            return [s2_ch1, s2_ch2]


class MockServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7100,
        mode: str = "signal",
        seed: int = 7,
        duration: int = 3600,
        corrupt_prob: float = 0.0,
        duplicate_prob: float = 0.0,
    ):
        self.host = host
        self.port = port
        self.generator = BiosignalGenerator(mode=mode, seed=seed, duration=duration)
        self.duration = duration
        self.session_id = uuid.uuid4().hex[:8]
        self.start_time: float = 0.0
        self.sensor_cursors = {1: 0, 2: 0}
        self.corrupt_prob = corrupt_prob
        self.duplicate_prob = duplicate_prob

    def get_current_max_index(self) -> int:
        elapsed = time.monotonic() - self.start_time
        return min(int(elapsed * 400), self.duration * 400)

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        peer = writer.get_extra_info("peername")
        print(f"[Mock] Client connected from {peer}, session={self.session_id}")

        last_reply_bytes: Optional[bytes] = None

        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break

                try:
                    req = json.loads(line.decode("utf-8"))
                except Exception:
                    continue

                cmd = req.get("cmd")
                if cmd != "read":
                    continue

                sensor = int(req.get("sensor", 1))
                if sensor not in (1, 2):
                    sensor = 1

                curr_max = self.get_current_max_index()

                # Check if client requested a specific from_index
                requested_from = req.get("from_index")
                if requested_from is not None:
                    req_idx = int(requested_from)
                else:
                    req_idx = self.sensor_cursors[sensor]

                # 10-second buffer check (4000 samples)
                buffer_floor = max(0, curr_max - 4000)
                lost_ranges = []
                if req_idx < buffer_floor:
                    lost_ranges.append(
                        {
                            "from_index": req_idx,
                            "to_index": buffer_floor - 1,
                            "reason": "buffer_overflow",
                        }
                    )
                    req_idx = buffer_floor

                # Cap return count at 400 samples (1 second)
                available = max(0, curr_max - req_idx)
                count = min(400, available)

                data = [
                    self.generator.generate_sample(sensor, req_idx + i)
                    for i in range(count)
                ]

                # Advance cursor
                self.sensor_cursors[sensor] = req_idx + count
                is_session_end = (req_idx + count >= self.duration * 400)

                # Format data JSON text to compute exact wire CRC-32
                data_text = json.dumps(data, separators=(",", ":"))
                crc_val = zlib.crc32(data_text.encode("utf-8")) & 0xFFFFFFFF
                crc32_hex = f"{crc_val:08x}"

                # Inject corrupt CRC if fault enabled
                if self.corrupt_prob > 0 and random.random() < self.corrupt_prob:
                    crc32_hex = "deadbeef"

                reply_dict = {
                    "session_id": self.session_id,
                    "sensor": sensor,
                    "first_index": req_idx,
                    "count": count,
                    "lost": lost_ranges,
                    "session_end": is_session_end,
                    "crc32": crc32_hex,
                    "data": data,
                }

                # Construct line with exactly matching data_text
                reply_bytes = (
                    json.dumps(reply_dict, separators=(",", ":")) + "\n"
                ).encode("utf-8")

                # Test fault: duplicate reply
                if (
                    self.duplicate_prob > 0
                    and last_reply_bytes is not None
                    and random.random() < self.duplicate_prob
                ):
                    writer.write(last_reply_bytes)
                    await writer.drain()

                writer.write(reply_bytes)
                await writer.drain()
                last_reply_bytes = reply_bytes

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[Mock] Connection error: {e}")
        finally:
            print(f"[Mock] Client disconnected: {peer}")
            writer.close()
            await writer.wait_closed()

    async def run(self):
        self.start_time = time.monotonic()
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets)
        print(
            f"[Mock] biosignal-mock running on {addrs} (session={self.session_id}, mode={self.generator.mode})"
        )
        async with server:
            await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Mock Biosignal Service")
    parser.add_argument(
        "--mode", choices=["signal", "ramp"], default="signal", help="Mode of data"
    )
    parser.add_argument("--seed", type=int, default=7, help="Random seed")
    parser.add_argument(
        "--duration", type=int, default=3600, help="Duration in seconds"
    )
    parser.add_argument("--port", type=int, default=7100, help="TCP port")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument(
        "--corrupt-prob", type=float, default=0.0, help="Probability of corrupt CRC"
    )
    parser.add_argument(
        "--duplicate-prob",
        type=float,
        default=0.0,
        help="Probability of duplicate reply",
    )
    args = parser.parse_args()

    server = MockServer(
        host=args.host,
        port=args.port,
        mode=args.mode,
        seed=args.seed,
        duration=args.duration,
        corrupt_prob=args.corrupt_prob,
        duplicate_prob=args.duplicate_prob,
    )
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\n[Mock] Stopped.")


if __name__ == "__main__":
    main()

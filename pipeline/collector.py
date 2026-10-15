import asyncio
import json
import logging
import sqlite3
import time
import zlib
from typing import Any, Dict, Optional, Tuple
from pipeline.config import MOCK_HOST, MOCK_PORT
from pipeline.storage import Storage

logger = logging.getLogger("pipeline.collector")


def extract_raw_data_slice(raw_line: bytes) -> Optional[bytes]:
    """
    Finds the exact slice of raw bytes from [ to matching ] for the 'data' field.
    CRC-32 must be computed over these exact wire bytes.
    """
    data_key_pos = raw_line.find(b'"data"')
    if data_key_pos == -1:
        return None
    first_bracket = raw_line.find(b"[", data_key_pos)
    if first_bracket == -1:
        return None

    bracket_count = 0
    last_bracket = -1
    for idx in range(first_bracket, len(raw_line)):
        b = raw_line[idx]
        if b == 91:  # ord('[')
            bracket_count += 1
        elif b == 93:  # ord(']')
            bracket_count -= 1
            if bracket_count == 0:
                last_bracket = idx
                break

    if last_bracket == -1:
        return None
    return raw_line[first_bracket : last_bracket + 1]


def validate_wire_crc(raw_line: bytes, expected_crc_hex: str) -> bool:
    data_slice = extract_raw_data_slice(raw_line)
    if data_slice is None:
        return False
    computed_crc = zlib.crc32(data_slice) & 0xFFFFFFFF
    try:
        expected_crc = int(expected_crc_hex, 16)
        return computed_crc == expected_crc
    except ValueError:
        return False


class SensorCollector:
    def __init__(
        self,
        sensor: int,
        storage: Storage,
        host: str = MOCK_HOST,
        port: int = MOCK_PORT,
    ):
        self.sensor = sensor
        self.storage = storage
        self.host = host
        self.port = port
        self.current_session_id: Optional[str] = self.storage.get_latest_session_id()
        if self.current_session_id:
            last_idx = self.storage.get_last_sample_index(self.current_session_id, self.sensor)
            self.next_index_needed: int = last_idx + 1 if last_idx >= 0 else 0
        else:
            self.next_index_needed: int = 0
        self.is_running: bool = False

    async def run(self):
        self.is_running = True
        logger.info(f"Collector sensor {self.sensor} starting...")

        while self.is_running:
            reader: Optional[asyncio.StreamReader] = None
            writer: Optional[asyncio.StreamWriter] = None
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
                logger.info(f"Sensor {self.sensor} connected to mock on {self.host}:{self.port}")

                while self.is_running:
                    # Request next batch
                    req = {"cmd": "read", "sensor": self.sensor}
                    if self.next_index_needed > 0:
                        req["from_index"] = self.next_index_needed

                    req_bytes = (json.dumps(req, separators=(",", ":")) + "\n").encode("utf-8")
                    writer.write(req_bytes)
                    await writer.drain()

                    raw_line = await reader.readline()
                    if not raw_line:
                        logger.warning(f"Sensor {self.sensor} mock connection closed.")
                        break

                    try:
                        reply = json.loads(raw_line.decode("utf-8"))
                    except Exception as e:
                        logger.error(f"Sensor {self.sensor} received unparseable JSON: {e}")
                        continue

                    # Validate wire CRC-32
                    expected_crc = reply.get("crc32", "")
                    if not validate_wire_crc(raw_line, expected_crc):
                        logger.warning(
                            f"Sensor {self.sensor} corrupt CRC detected! Discarding reply and re-reading from {self.next_index_needed}"
                        )
                        # Immediately loop and re-read from next_index_needed
                        continue

                    session_id = reply["session_id"]

                    # Check for session restart
                    if self.current_session_id is not None and session_id != self.current_session_id:
                        logger.warning(
                            f"Sensor {self.sensor} session change detected: old={self.current_session_id}, new={session_id}"
                        )
                        # Log old session's unread data as lost with to_index: null
                        try:
                            self.storage.insert_loss(
                                session_id=self.current_session_id,
                                sensor=self.sensor,
                                from_index=self.next_index_needed,
                                to_index=None,
                                reason="session_ended_unread",
                            )
                        except (sqlite3.Error, OSError) as e:
                            logger.error(f"Sensor {self.sensor} write error on insert_loss: {e}")
                            self.storage.record_write_error(str(e))
                        self.next_index_needed = 0

                    self.current_session_id = session_id

                    # 1. Process any losses reported by mock
                    lost_ranges = reply.get("lost", [])
                    if lost_ranges:
                        try:
                            self.storage.insert_losses_batch(session_id, self.sensor, lost_ranges)
                        except (sqlite3.Error, OSError) as e:
                            logger.error(f"Sensor {self.sensor} write error on insert_losses_batch: {e}")
                            self.storage.record_write_error(str(e))

                    # 2. Process data
                    first_index = reply["first_index"]
                    count = reply["count"]
                    data = reply.get("data", [])

                    if count > 0 and data:
                        try:
                            self.storage.insert_samples_batch(session_id, self.sensor, first_index, data)
                        except (sqlite3.Error, OSError) as e:
                            logger.error(
                                f"Sensor {self.sensor} SQLite write error (disk full / IO error): {e}"
                            )
                            self.storage.record_write_error(str(e))
                        self.next_index_needed = first_index + count
                    elif lost_ranges:
                        # Advance next_index_needed past lost ranges if no data
                        max_lost_to = max(
                            (l["to_index"] for l in lost_ranges if l.get("to_index") is not None),
                            default=self.next_index_needed - 1,
                        )
                        if max_lost_to >= self.next_index_needed:
                            self.next_index_needed = max_lost_to + 1

                    # Check session end
                    if reply.get("session_end"):
                        logger.info(f"Sensor {self.sensor} session {session_id} finished (session_end=True)")
                        # Wait for potential new session
                        await asyncio.sleep(0.5)

                    # Flow control: if count == 0, we are fully caught up with mock, sleep briefly
                    if count == 0:
                        await asyncio.sleep(0.05)

            except (ConnectionRefusedError, OSError) as e:
                logger.debug(f"Sensor {self.sensor} connection to mock failed: {e}. Retrying in 1s...")
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                self.is_running = False
                break
            except Exception as e:
                logger.error(f"Sensor {self.sensor} unexpected error: {e}", exc_info=True)
                await asyncio.sleep(0.5)
            finally:
                if writer:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass


class CollectorManager:
    """Manages concurrent collection for Sensor 1 and Sensor 2."""

    def __init__(
        self,
        storage: Storage,
        host: str = MOCK_HOST,
        port: int = MOCK_PORT,
    ):
        self.storage = storage
        self.collector1 = SensorCollector(1, storage, host, port)
        self.collector2 = SensorCollector(2, storage, host, port)
        self._tasks = []

    async def start(self):
        self._tasks = [
            asyncio.create_task(self.collector1.run()),
            asyncio.create_task(self.collector2.run()),
        ]
        await asyncio.gather(*self._tasks)

    def stop(self):
        self.collector1.is_running = False
        self.collector2.is_running = False
        for t in self._tasks:
            t.cancel()

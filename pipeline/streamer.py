import asyncio
import logging
from typing import Dict, List, Optional
import httpx
from pipeline.config import (
    CATCHUP_BATCH_SIZE,
    CLOUD_URL,
    ONLINE_BATCH_SIZE,
    WINDOW_SIZE,
)
from pipeline.mode_controller import ModeController
from pipeline.processing import process_window
from pipeline.storage import Storage

logger = logging.getLogger("pipeline.streamer")


class StreamerManager:
    """
    Handles streaming to cloud in ONLINE mode, local window processing in OFFLINE mode,
    and fast catch-up when transitioning OFFLINE -> ONLINE.
    """

    def __init__(
        self,
        storage: Storage,
        mode_controller: ModeController,
        cloud_url: str = CLOUD_URL,
    ):
        self.storage = storage
        self.mode_controller = mode_controller
        self.cloud_url = cloud_url.rstrip("/")
        self.ingest_url = f"{self.cloud_url}/ingest"
        self.is_running: bool = False
        self.is_catching_up: bool = False

        self.last_processed_offline_window = -1

        # Register callbacks
        self.mode_controller.on_enter_online(self.trigger_catch_up)
        self.mode_controller.on_enter_offline(self.on_offline)

    def trigger_catch_up(self):
        logger.info("Triggering Catch-Up Agent...")
        asyncio.create_task(self._run_catchup())

    def on_offline(self):
        logger.info("Offline mode active: Local processing enabled.")

    async def _send_batch(
        self,
        client: httpx.AsyncClient,
        session_id: str,
        sensor: int,
        samples: List[tuple],
    ) -> bool:
        if not samples:
            return True

        first_index = samples[0][0]
        data = [s[1] for s in samples]
        last_index = samples[-1][0]

        losses = self.storage.get_losses_for_sensor(session_id, sensor)

        payload = {
            "session_id": session_id,
            "sensor": sensor,
            "first_index": first_index,
            "data": data,
        }
        if losses:
            payload["losses"] = losses

        try:
            resp = await client.post(self.ingest_url, json=payload, timeout=10.0)
            if resp.status_code == 200:
                self.storage.update_last_synced_index(session_id, sensor, last_index)
                return True
            else:
                logger.warning(f"Cloud ingest returned status {resp.status_code}")
                return False
        except Exception as e:
            logger.debug(f"Cloud ingest request failed: {e}")
            return False

    async def _run_catchup(self):
        if self.is_catching_up:
            return
        self.is_catching_up = True
        logger.info("Catch-Up Agent started: draining backlog with large batches...")

        session_id = self.storage.get_latest_session_id()
        if not session_id:
            self.is_catching_up = False
            return

        async with httpx.AsyncClient() as client:
            while self.mode_controller.current_mode == "ONLINE" and self.is_running:
                any_sent = False
                for sensor in (1, 2):
                    last_synced = self.storage.get_last_synced_index(session_id, sensor)
                    unsynced = self.storage.get_unsynced_samples(
                        session_id, sensor, last_synced, limit=CATCHUP_BATCH_SIZE
                    )
                    if unsynced:
                        success = await self._send_batch(client, session_id, sensor, unsynced)
                        if success:
                            any_sent = True
                        else:
                            # Break if connection issue
                            break

                if not any_sent:
                    # Sync any losses for both sensors
                    for s_id in (1, 2):
                        l_recs = self.storage.get_losses_for_sensor(session_id, s_id)
                        if l_recs:
                            try:
                                await client.post(
                                    self.ingest_url,
                                    json={
                                        "session_id": session_id,
                                        "sensor": s_id,
                                        "first_index": 0,
                                        "data": [],
                                        "losses": l_recs,
                                    },
                                    timeout=5.0,
                                )
                            except Exception:
                                pass
                    logger.info("Catch-Up Agent: All backlog caught up!")
                    break
                await asyncio.sleep(0.01)

        self.is_catching_up = False

    async def _run_offline_processor(self):
        """
        Runs Section 3 processing logic locally when in OFFLINE mode.
        """
        session_id = self.storage.get_latest_session_id()
        if not session_id:
            return

        # Check maximum available sample index
        last_s1 = self.storage.get_last_sample_index(session_id, 1)
        last_s2 = self.storage.get_last_sample_index(session_id, 2)
        min_last = min(last_s1, last_s2)
        if min_last < 0:
            return

        max_available_window = min_last // WINDOW_SIZE

        # Process each window that hasn't been processed yet
        start_w = max(0, self.last_processed_offline_window + 1)
        for w in range(start_w, max_available_window + 1):
            w_start = w * WINDOW_SIZE
            w_end = w_start + WINDOW_SIZE - 1

            s1_samples = self.storage.get_samples_range(session_id, 1, w_start, w_end)
            s2_samples = self.storage.get_samples_range(session_id, 2, w_start, w_end)

            # For HR, fetch 5 seconds of sensor 1 history (from 400*(w-4) to 400*(w+1)-1)
            history_dict = {}
            if w >= 4:
                hr_start = 400 * (w - 4)
                hr_end = 400 * (w + 1) - 1
                hr_samples = self.storage.get_samples_range(session_id, 1, hr_start, hr_end)
                history_dict = {idx: vals[1] for idx, vals in hr_samples.items()}

            result = process_window(
                w, s1_samples, s2_samples, mode="offline", s1_history_ch2=history_dict
            )
            self.storage.save_result(session_id, w, "offline", result)
            self.last_processed_offline_window = w

    async def run(self):
        self.is_running = True
        logger.info("StreamerManager started.")

        async with httpx.AsyncClient() as client:
            while self.is_running:
                session_id = self.storage.get_latest_session_id()

                if self.mode_controller.current_mode == "ONLINE":
                    if session_id and not self.is_catching_up:
                        # Stream 1-second batch per sensor
                        for sensor in (1, 2):
                            last_synced = self.storage.get_last_synced_index(session_id, sensor)
                            unsynced = self.storage.get_unsynced_samples(
                                session_id, sensor, last_synced, limit=ONLINE_BATCH_SIZE
                            )
                            if unsynced:
                                await self._send_batch(client, session_id, sensor, unsynced)
                else:
                    # In OFFLINE mode: run local offline processor
                    await self._run_offline_processor()

                try:
                    await asyncio.sleep(0.2)
                except asyncio.CancelledError:
                    self.is_running = False
                    break

    def stop(self):
        self.is_running = False

import asyncio
import logging
from typing import Callable, List, Optional
import httpx
from pipeline.config import (
    CLOUD_URL,
    HEALTH_PROBE_INTERVAL,
    MAX_FAILURES_BEFORE_OFFLINE,
    PROBE_TIMEOUT,
)

logger = logging.getLogger("pipeline.mode_controller")


class ModeController:
    """
    State machine for ONLINE <-> OFFLINE mode switching.
    Probes cloud health endpoint every 5s.
    2 consecutive failures -> OFFLINE (<= 10s).
    1 success -> ONLINE, triggers catch-up.
    """

    def __init__(
        self,
        cloud_url: str = CLOUD_URL,
        probe_interval: float = HEALTH_PROBE_INTERVAL,
        max_failures: int = MAX_FAILURES_BEFORE_OFFLINE,
        probe_timeout: float = PROBE_TIMEOUT,
    ):
        self.cloud_url = cloud_url.rstrip("/")
        self.health_url = f"{self.cloud_url}/health"
        self.probe_interval = probe_interval
        self.max_failures = max_failures
        self.probe_timeout = probe_timeout

        self.current_mode: str = "OFFLINE"
        self.consecutive_failures: int = 0
        self.is_running: bool = False

        self._on_enter_online: List[Callable[[], None]] = []
        self._on_enter_offline: List[Callable[[], None]] = []

    def on_enter_online(self, callback: Callable[[], None]) -> None:
        self._on_enter_online.append(callback)

    def on_enter_offline(self, callback: Callable[[], None]) -> None:
        self._on_enter_offline.append(callback)

    async def probe_once(self, client: httpx.AsyncClient) -> bool:
        try:
            resp = await client.get(self.health_url, timeout=self.probe_timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def transition_to_online(self) -> None:
        """Switch state to ONLINE and notify callbacks."""
        self.consecutive_failures = 0
        if self.current_mode != "ONLINE":
            logger.info("Cloud reachable! Switching state: OFFLINE -> ONLINE")
            self.current_mode = "ONLINE"
            for cb in self._on_enter_online:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        asyncio.create_task(cb())
                    else:
                        cb()
                except Exception as e:
                    logger.error(f"Error in on_enter_online callback: {e}")

    def transition_to_offline(self) -> None:
        """Switch state to OFFLINE and notify callbacks."""
        if self.current_mode != "OFFLINE":
            logger.warning("Cloud unreachable! Switching state: ONLINE -> OFFLINE")
            self.current_mode = "OFFLINE"
            for cb in self._on_enter_offline:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        asyncio.create_task(cb())
                    else:
                        cb()
                except Exception as e:
                    logger.error(f"Error in on_enter_offline callback: {e}")

    async def run(self):
        self.is_running = True
        logger.info(f"ModeController started. Probing {self.health_url} every {self.probe_interval}s...")

        async with httpx.AsyncClient() as client:
            while self.is_running:
                is_healthy = await self.probe_once(client)

                if is_healthy:
                    self.transition_to_online()
                else:
                    self.consecutive_failures += 1
                    logger.debug(
                        f"Health probe failed ({self.consecutive_failures}/{self.max_failures})"
                    )
                    if self.consecutive_failures >= self.max_failures:
                        self.transition_to_offline()

                try:
                    await asyncio.sleep(self.probe_interval)
                except asyncio.CancelledError:
                    self.is_running = False
                    break

    def stop(self):
        self.is_running = False

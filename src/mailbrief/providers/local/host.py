"""One lazily started local model, shared by desktop runs and stopped after idle time."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from mailbrief.providers.local.provider import Endpoint
from mailbrief.providers.local.server import NOT_READY, LlamaServer, unavailable
from mailbrief.services.local_setup import LocalSetup, LocalSetupError, SetupState

logger = logging.getLogger(__name__)


class LocalModelHost:
    """A checked setup is available without loading it; the first lease starts it.

    The caller cancels its active operation before close. Setup never downloads here.
    Only one lease can use the process, and the idle task never outlives this host.
    """

    def __init__(
        self,
        setup: LocalSetup,
        *,
        idle_seconds: float = 300.0,
        record: Path | None = None,
        server_factory: Callable[..., LlamaServer] = LlamaServer,
    ) -> None:
        self._setup = setup
        self._idle_seconds = idle_seconds
        self._server_factory = server_factory
        self._record = record
        self._server: LlamaServer | None = None
        self._idle: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def available(self) -> bool:
        if self._closed:
            return False
        try:
            return (await self._setup.status()).state is SetupState.READY
        except (OSError, LocalSetupError):
            logger.info("Local model setup is unavailable")
            return False

    async def _cancel_idle(self) -> None:
        task, self._idle = self._idle, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            stopping = asyncio.create_task(server.stop())
            try:
                await asyncio.shield(stopping)
            except asyncio.CancelledError:
                await stopping
                raise

    async def _stop_after_idle(self) -> None:
        await asyncio.sleep(self._idle_seconds)
        async with self._lock:
            await self._stop()

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Endpoint]:
        async with self._lock:
            # The previous lease schedules its idle task just before releasing the
            # lock. Cancel here so a queued lease cannot miss that new timer.
            await self._cancel_idle()
            if self._closed:
                raise unavailable(NOT_READY)
            status = await self._setup.status()
            if (
                status.state is not SetupState.READY
                or status.server is None
                or status.model is None
            ):
                raise unavailable(NOT_READY)
            if self._server is None or not self._server.running:
                await self._stop()
                self._server = self._server_factory(
                    [str(status.server)],
                    status.model,
                    context=8192,
                    gpu_layers=99,
                    record=self._record,
                )
                try:
                    await self._server.start()
                except BaseException:
                    await self._stop()
                    raise
            try:
                async with self._server.lease() as endpoint:
                    yield endpoint
            except BaseException:
                # Cancelling the HTTP request alone need not stop inference. Join
                # the process before another lease can start or publish a result.
                await self._stop()
                raise
            finally:
                if not self._closed:
                    self._idle = asyncio.create_task(self._stop_after_idle())

    async def close(self) -> None:
        self._closed = True
        await self._cancel_idle()
        async with self._lock:
            await self._stop()

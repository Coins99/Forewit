"""The retained model starts on demand and never survives stop, idle or close."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from mailbrief.ports.errors import ProviderUnavailableError
from mailbrief.providers.local.host import LocalModelHost
from mailbrief.providers.local.provider import Endpoint
from mailbrief.services.local_setup import SetupState, SetupStatus


class Server:
    def __init__(self) -> None:
        self.running = False
        self.starts = 0
        self.stops = 0

    async def start(self) -> None:
        self.starts += 1
        self.running = True

    async def stop(self) -> None:
        self.stops += 1
        self.running = False

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Endpoint]:
        assert self.running
        yield Endpoint("http://127.0.0.1:12345", "synthetic")


def setup() -> Mock:
    return Mock(
        status=AsyncMock(
            return_value=SetupStatus(SetupState.READY, server=Path("server"), model=Path("model"))
        )
    )


async def test_status_is_lazy_and_leases_reuse_one_process() -> None:
    server = Server()
    factory = Mock(return_value=server)
    host = LocalModelHost(setup(), server_factory=factory)
    assert await host.available()
    factory.assert_not_called()
    try:
        for _ in range(2):
            async with host.lease() as endpoint:
                assert endpoint.url == "http://127.0.0.1:12345"
        assert server.starts == 1
        assert server.stops == 0
    finally:
        await host.close()
    assert server.stops == 1
    assert not await host.available()
    with pytest.raises(ProviderUnavailableError):
        async with host.lease():
            pytest.fail("Closed host leased")


async def test_idle_stops_and_next_lease_restarts() -> None:
    first, second = Server(), Server()
    host = LocalModelHost(setup(), server_factory=Mock(side_effect=[first, second]), idle_seconds=0)
    try:
        async with host.lease():
            pass
        for _ in range(10):
            if first.stops:
                break
            await asyncio.sleep(0)
        assert first.stops == 1
        async with host.lease():
            assert second.running
    finally:
        await host.close()
    assert second.stops == 1


async def test_queued_lease_cancels_the_previous_idle_timer() -> None:
    server = Server()
    host = LocalModelHost(setup(), server_factory=Mock(return_value=server), idle_seconds=0)
    entered = asyncio.Event()

    async def waiting() -> None:
        async with host.lease():
            entered.set()
            for _ in range(5):
                await asyncio.sleep(0)
            assert server.running

    try:
        async with host.lease():
            task = asyncio.create_task(waiting())
            await asyncio.sleep(0)
        await task
        assert entered.is_set()
        assert server.starts == 1
    finally:
        await host.close()


async def test_cancelled_lease_stops_inference_before_returning() -> None:
    server = Server()
    host = LocalModelHost(setup(), server_factory=Mock(return_value=server))
    entered = asyncio.Event()

    async def work() -> None:
        async with host.lease():
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert server.stops == 1
    assert not server.running
    await host.close()


async def test_missing_setup_never_constructs_a_server() -> None:
    missing = setup()
    missing.status.return_value = SetupStatus(SetupState.NOT_SET_UP)
    factory = Mock()
    host = LocalModelHost(missing, server_factory=factory)
    try:
        assert not await host.available()
        with pytest.raises(ProviderUnavailableError):
            async with host.lease():
                pytest.fail("Uninstalled model leased")
        factory.assert_not_called()
    finally:
        await host.close()


async def test_close_during_idle_shutdown_joins_the_process_stop() -> None:
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class SlowStop(Server):
        async def stop(self) -> None:
            entered.set()
            await release.wait()
            await super().stop()
            finished.set()

    server = SlowStop()
    host = LocalModelHost(setup(), server_factory=Mock(return_value=server), idle_seconds=0)
    async with host.lease():
        pass
    await entered.wait()
    closing = asyncio.create_task(host.close())
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    await closing
    assert finished.is_set()
    assert not server.running

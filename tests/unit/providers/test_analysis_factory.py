"""Mode selection cannot fall through to the cloud, including missing-model paths."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from mailbrief.config import Settings
from mailbrief.domain.preferences import OwnerPreferences
from mailbrief.ports.ai_provider import AIProvider
from mailbrief.providers import analysis


async def test_cloud_uses_existing_factory(tmp_path: Path) -> None:
    provider = Mock()
    closed = False

    @asynccontextmanager
    async def cloud(settings: Settings) -> AsyncIterator[AIProvider]:
        nonlocal closed
        try:
            yield provider
        finally:
            closed = True

    async with analysis.analysis_provider(
        Settings(), OwnerPreferences.defaults(), folder=tmp_path, cloud=cloud
    ) as selected:
        assert selected is provider
    assert closed


@pytest.mark.parametrize("shared", [False, True])
async def test_unavailable_local_never_constructs_cloud_or_starts_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shared: bool
) -> None:
    host = Mock(available=AsyncMock(return_value=False), close=AsyncMock())
    constructor = Mock(return_value=host)
    monkeypatch.setattr(analysis, "LocalModelHost", constructor)
    cloud = Mock(side_effect=AssertionError("Local analysis entered cloud"))
    preferences = OwnerPreferences.defaults().model_copy(update={"analysis_mode": "local"})
    async with analysis.analysis_provider(
        Settings(experimental_local_ai=True),
        preferences,
        folder=tmp_path,
        cloud=cloud,
        host=host if shared else None,
    ) as selected:
        assert selected.provider_name == "local"
        assert not await selected.credentials_available()
        host.lease.assert_not_called()
    cloud.assert_not_called()
    assert host.close.await_count == (0 if shared else 1)


async def test_disabled_local_never_constructs_either_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailbrief.errors import ConfigurationError

    local = Mock(side_effect=AssertionError("Local host constructed while disabled"))
    cloud = Mock(side_effect=AssertionError("Local mode fell back to cloud"))
    monkeypatch.setattr(analysis, "LocalModelHost", local)
    preferences = OwnerPreferences.defaults().model_copy(update={"analysis_mode": "local"})
    with pytest.raises(ConfigurationError, match="experimental and disabled"):
        async with analysis.analysis_provider(
            Settings(), preferences, folder=tmp_path, cloud=cloud
        ):
            pytest.fail("Disabled local mode yielded a provider")
    local.assert_not_called()
    cloud.assert_not_called()

"""Select the email analysis adapter without entering a cloud factory in local mode."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import httpx

from mailbrief.config import Settings
from mailbrief.domain.preferences import AnalysisMode, OwnerPreferences
from mailbrief.ports.ai_provider import AIProvider
from mailbrief.providers.groq.factory import groq_provider
from mailbrief.providers.local.host import LocalModelHost
from mailbrief.providers.local.manifest import MODEL, manifest_for
from mailbrief.providers.local.provider import LocalModelProvider
from mailbrief.providers.local.transport import LoopbackGuard
from mailbrief.services.local_setup import LocalSetup

# One message per local call. Smaller owner limits still apply.
LOCAL_OUTPUT_TOKENS_MAX = 2_048


@asynccontextmanager
async def analysis_provider(
    settings: Settings,
    preferences: OwnerPreferences,
    *,
    folder: Path,
    host: LocalModelHost | None = None,
    cloud: Callable[[Settings], AbstractAsyncContextManager[AIProvider]] = groq_provider,
) -> AsyncIterator[AIProvider]:
    """The desktop retains its host; a CLI-owned host is closed on every exit path."""
    if preferences.analysis_mode is AnalysisMode.CLOUD:
        async with cloud(settings) as provider:
            yield provider
        return
    settings.require_local_ai()
    own_host = host is None
    local = host or LocalModelHost(
        LocalSetup(folder, manifest_for()), record=folder / "server.json"
    )
    try:
        async with httpx.AsyncClient(transport=LoopbackGuard(), trust_env=False) as http:
            yield LocalModelProvider(
                http,
                local,
                model=MODEL.name,
                max_output_tokens=min(settings.ai_max_output_tokens, LOCAL_OUTPUT_TOKENS_MAX),
                max_requests=settings.ai_max_requests_per_run,
                timeout=httpx.Timeout(settings.ai_timeout_seconds, connect=10.0),
            )
    finally:
        if own_host:
            await local.close()

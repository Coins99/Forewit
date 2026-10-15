"""Production composition for Gmail authentication and metadata synchronization."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

import httpx

from mailbrief.config import Settings
from mailbrief.domain.bodies import MessageBody
from mailbrief.errors import ConfigurationError
from mailbrief.providers.gmail.auth import GmailAuth
from mailbrief.providers.gmail.cache import GmailCredentialStore
from mailbrief.providers.gmail.client import GmailClient
from mailbrief.providers.gmail.errors import GmailSetupError
from mailbrief.providers.gmail.oauth import DesktopClient, LoopbackAuthorization
from mailbrief.providers.gmail.provider import GmailProvider


@asynccontextmanager
async def gmail_auth(settings: Settings) -> AsyncIterator[GmailAuth]:
    """Own network resources and require the OS credential vault; no token files."""
    try:
        path = settings.require_gmail_oauth_client_path()
    except ConfigurationError as exc:
        raise GmailSetupError(str(exc)) from None
    client = await asyncio.to_thread(DesktopClient.load, path)
    store = GmailCredentialStore()
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as http:
        yield GmailAuth(client, store, http, LoopbackAuthorization())


@asynccontextmanager
async def gmail_provider(
    settings: Settings, *, silent_only: bool = False
) -> AsyncIterator[GmailProvider]:
    """Compose metadata access without exposing provider resources to core services."""
    async with (
        gmail_auth(settings) as auth,
        httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as http,
    ):
        yield GmailProvider(auth, GmailClient(http, auth), silent_only=silent_only)


class GmailOpener(Protocol):
    """Opens Gmail for a block: gmail_provider, or a stand-in for it."""

    def __call__(
        self, settings: Settings, *, silent_only: bool = False
    ) -> AbstractAsyncContextManager[GmailProvider]: ...


class GmailBodies:
    """A body source that opens Gmail for one download and closes it again, so nothing
    touches Gmail until a body is wanted: AI drafting's email, once the owner chooses it.

    ``open_gmail`` is the caller's own gmail_provider, so whatever opens Gmail for the
    caller opens it for the download too.
    """

    def __init__(
        self, open_gmail: GmailOpener, settings: Settings, *, silent_only: bool = True
    ) -> None:
        self._open_gmail = open_gmail
        self._settings = settings
        self._silent_only = silent_only

    async def fetch_message_body(self, provider_message_id: str) -> MessageBody:
        async with self._open_gmail(self._settings, silent_only=self._silent_only) as provider:
            return await provider.fetch_message_body(provider_message_id)

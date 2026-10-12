"""Compose Google Calendar from the same Desktop OAuth client as Gmail, with its own grant."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from mailbrief.config import Settings
from mailbrief.errors import ConfigurationError
from mailbrief.providers.gcal.auth import CALENDAR_SCOPES, CalendarAuth, CalendarCredentialStore
from mailbrief.providers.gcal.client import CalendarClient
from mailbrief.providers.gcal.errors import CalendarSetupError
from mailbrief.providers.gcal.reader import GoogleCalendarReader
from mailbrief.providers.gmail.oauth import DesktopClient, LoopbackAuthorization


@asynccontextmanager
async def google_calendar(settings: Settings) -> AsyncIterator[GoogleCalendarReader]:
    """Own the network resources; the sign-in lives only in the OS vault."""
    try:
        path = settings.require_gmail_oauth_client_path()
    except ConfigurationError:
        raise CalendarSetupError(
            "Choose the Google Desktop OAuth client JSON in Settings first."
        ) from None
    try:
        client = await asyncio.to_thread(DesktopClient.load, path)
    except ConfigurationError:
        # Its own message names Gmail's setup; say what the calendar needs instead.
        raise CalendarSetupError(
            "The Google Desktop OAuth client JSON chosen in Settings can't be used; choose a "
            "valid Desktop app client file in Settings."
        ) from None
    store = await asyncio.to_thread(CalendarCredentialStore)
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as http:
        auth = CalendarAuth(client, store, http, LoopbackAuthorization(scopes=CALENDAR_SCOPES))
        yield GoogleCalendarReader(auth, CalendarClient(http, auth))

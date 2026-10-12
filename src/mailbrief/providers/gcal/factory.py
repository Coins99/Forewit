"""Compose Google Calendar over the one Google sign-in (ADR 0019).

Calendar requests use the Gmail sign-in's session for tokens, and their own bounded client.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from mailbrief.config import Settings
from mailbrief.providers.gcal.client import CalendarClient
from mailbrief.providers.gcal.reader import GoogleCalendarReader
from mailbrief.providers.gmail.factory import gmail_auth


@asynccontextmanager
async def google_calendar(settings: Settings) -> AsyncIterator[GoogleCalendarReader]:
    """Own the network resources; the sign-in lives only in the OS vault, under Gmail's entry."""
    async with (
        gmail_auth(settings) as auth,
        httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as http,
    ):
        yield GoogleCalendarReader(auth, CalendarClient(http, auth))

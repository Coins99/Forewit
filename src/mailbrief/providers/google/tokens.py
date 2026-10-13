"""Google's OAuth token endpoint, shared by the Gmail and Google Calendar sign-ins.

Each sign-in supplies its own scopes, fixed messages and status check; nothing the server
sends is ever put in a message.
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from mailbrief.ports.errors import AuthenticationRequiredError, ProviderPermissionError

TOKEN_URL: Final = "https://oauth2.googleapis.com/token"


class TokenResponse(BaseModel):
    """Validate token responses without putting sensitive input in errors/reprs."""

    model_config = ConfigDict(hide_input_in_errors=True)

    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr | None = Field(default=None, min_length=1)
    expires_in: int = Field(gt=0, le=86_400, strict=True)
    token_type: str
    scope: str | None = None


@dataclass(frozen=True, slots=True)
class TokenMessages:
    """One sign-in's fixed texts for the token endpoint's failures."""

    unreachable: str
    expired: str
    bad_client: str
    unauthorized_client: str
    invalid: str
    token_type: str
    missing_scope: str
    extra_scope: str


async def exchange_tokens(
    http: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    fields: Mapping[str, str],
    *,
    scopes: Collection[str],
    allowed: Collection[str],
    messages: TokenMessages,
    response_error: Callable[[str], Exception],
    setup_error: Callable[[str], Exception],
    check_status: Callable[[httpx.Response], None],
) -> TokenResponse:
    """Post ``fields`` with the client's credentials and validate the answer: a bearer
    token whose granted scopes, when Google lists them, include every one of ``scopes`` and
    nothing outside ``allowed``. A wider grant is refused, never saved: the sign-in's
    read-only promise has to hold for the credential, not only for the code."""
    data = {**fields, "client_id": client_id, "client_secret": client_secret}
    try:
        response = await http.post(TOKEN_URL, data=data, follow_redirects=False)
    except httpx.HTTPError:
        raise response_error(messages.unreachable) from None
    if response.status_code in {400, 401}:
        try:
            error = response.json()
        except ValueError:
            error = None
        reason = error.get("error") if isinstance(error, dict) else None
        if reason == "invalid_grant":
            raise AuthenticationRequiredError(messages.expired)
        if reason in ("invalid_client", "deleted_client"):
            raise setup_error(messages.bad_client)
        if reason == "unauthorized_client":
            raise setup_error(messages.unauthorized_client)
    check_status(response)
    try:
        tokens = TokenResponse.model_validate_json(response.content)
    except ValidationError:
        raise response_error(messages.invalid) from None
    if tokens.token_type.casefold() != "bearer":
        raise response_error(messages.token_type)
    granted = set(() if tokens.scope is None else tokens.scope.split())
    if tokens.scope is not None and not set(scopes) <= granted:
        raise ProviderPermissionError(messages.missing_scope)
    if granted - set(allowed):
        raise ProviderPermissionError(messages.extra_scope)
    return tokens

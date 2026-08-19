"""Service account credentials -> OAuth2 access tokens.

google-auth is used only to sign the JWT assertion; the token exchange and every
subsequent API call go over httpx. Its bundled transports pull in `requests`,
which would put a second, synchronous HTTP stack in an asyncio service for no
benefit.

A service account is used rather than user OAuth because this runs unattended:
there is no browser to complete a consent flow, and an unpublished app's refresh
token expires after seven days, which would silently stop ingestion.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
from google.auth import crypt, jwt

# Read-only is deliberate. The poller never writes, and a key that cannot write
# cannot corrupt the customer's Drive no matter what a bug does.
READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

# Full access, used only by the seeder when it creates source-side fixtures.
# Deliberately a separate constant from the one the poller asks for: the
# ingestion path must never hold a credential that can write, for the same
# reason Slack's seeder uses a different token than its ingestor.
WRITE_SCOPE = "https://www.googleapis.com/auth/drive"

GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"

# Assertions are valid for an hour; Google rejects anything longer.
ASSERTION_TTL_SECONDS = 3600
# Refresh this far ahead of expiry so an in-flight request cannot be signed with
# a token that expires mid-call.
REFRESH_SKEW_SECONDS = 120


class DriveAuthError(RuntimeError):
    pass


class ServiceAccountAuth:
    """Mints and caches access tokens for one service account key."""

    def __init__(
        self, key_path: str, *, scopes: tuple[str, ...] = (READONLY_SCOPE,)
    ) -> None:
        path = Path(key_path)
        if not path.is_file():
            raise DriveAuthError(f"service account key not found: {key_path}")

        info = json.loads(path.read_text(encoding="utf-8"))
        for field in ("client_email", "private_key", "token_uri"):
            if not info.get(field):
                raise DriveAuthError(f"key file is missing {field!r}")

        self.client_email: str = info["client_email"]
        self.project_id: str | None = info.get("project_id")
        self._token_uri: str = info["token_uri"]
        self._signer = crypt.RSASigner.from_service_account_info(info)
        self._scopes = scopes

        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def token(self, http: httpx.AsyncClient) -> str:
        """Current access token, refreshing when close to expiry.

        The lock makes concurrent callers share one refresh instead of each
        minting their own; token endpoints are quota-metered too.
        """
        if self._token and time.monotonic() < self._expires_at:
            return self._token

        async with self._lock:
            # Another coroutine may have refreshed while this one waited.
            if self._token and time.monotonic() < self._expires_at:
                return self._token
            return await self._refresh(http)

    async def _refresh(self, http: httpx.AsyncClient) -> str:
        now = int(time.time())
        assertion = jwt.encode(
            self._signer,
            {
                "iss": self.client_email,
                "scope": " ".join(self._scopes),
                "aud": self._token_uri,
                "iat": now,
                "exp": now + ASSERTION_TTL_SECONDS,
            },
        ).decode()

        response = await http.post(
            self._token_uri,
            data={"grant_type": GRANT_TYPE, "assertion": assertion},
        )
        if response.status_code >= 400:
            # Surface Google's own error text: 'invalid_grant' here almost always
            # means the machine clock is skewed, which is otherwise baffling.
            raise DriveAuthError(
                f"token exchange failed: HTTP {response.status_code} {response.text[:300]}"
            )

        body = response.json()
        token = body.get("access_token")
        if not token:
            raise DriveAuthError(f"token response had no access_token: {body}")

        self._token = token
        self._expires_at = time.monotonic() + int(body.get("expires_in", 3600)) - REFRESH_SKEW_SECONDS
        return token

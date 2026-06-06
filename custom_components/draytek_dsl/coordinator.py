"""DrayTek DSL coordinator: authenticates, scrapes line stats, parses values."""
from __future__ import annotations

import base64
import html as _html
import logging
import re
from datetime import timedelta
from urllib.parse import quote_plus

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, DEFAULT_SCAN_INTERVAL, DSL_STATUS_PATHS, PHYSICAL_CONNECTION_PATH

_LOGGER = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")

# Patterns for the Physical Connection page (v2x00.cgi?fid=168).
# Speeds are embedded as bps literals: KseparatorAdd('4998000')
# The upload value appears first, download second, both immediately after "SHOWTIME".
_PHYCONN_SPEED_RE = re.compile(
    r"SHOWTIME</font></td>.*?KseparatorAdd\('(\d+)'\).*?KseparatorAdd\('(\d+)'\)",
    re.DOTALL | re.IGNORECASE,
)
# SNR values are server-injected as integer dB literals in the VDSL JS block.
# The first match is SNR Upstream, the second is SNR Downstream.
_PHYCONN_SNR_RE = re.compile(r'">(\d+)\s*\(dB\)</font></td>', re.IGNORECASE)

# Fallback patterns for the DSL Status .sht pages (speeds only, no SNR).
_DS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"Actual Rate[^\d]*?(\d+)\s*[Kk]bps", re.IGNORECASE),
    re.compile(r"Down\s*Speed[^\d]*?(\d+)\s*[Kk]bps", re.IGNORECASE),
    re.compile(r"Down(?:stream)?\s*(?:Actual\s*)?Rate[^\d]*?(\d+)\s*[Kk]bps", re.IGNORECASE),
    re.compile(r"\bDS\s*(?:Actual\s*)?Rate[^\d]*?(\d+)\s*[Kk]bps", re.IGNORECASE),
    re.compile(r"Downstream[^\d]*?(\d+)\s*[Kk]bps", re.IGNORECASE),
]
_US_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"Actual Rate[^\d]*?\d+[^\d]*?[Kk]bps[^\d]*?(\d+)\s*[Kk]bps", re.IGNORECASE),
    re.compile(r"Up\s*Speed[^\d]*?(\d+)\s*[Kk]bps", re.IGNORECASE),
    re.compile(r"Up(?:stream)?\s*(?:Actual\s*)?Rate[^\d]*?(\d+)\s*[Kk]bps", re.IGNORECASE),
    re.compile(r"\bUS\s*(?:Actual\s*)?Rate[^\d]*?(\d+)\s*[Kk]bps", re.IGNORECASE),
    re.compile(r"Upstream[^\d]*?(\d+)\s*[Kk]bps", re.IGNORECASE),
]

LineData = dict[str, int | None]


def _encode_credential(value: str) -> str:
    """Base64-encode then URL-encode a credential, as expected by wlogin.cgi."""
    return quote_plus(base64.b64encode(value.encode()).decode())


def _parse_physical_connection(html: str) -> LineData:
    """Parse the Physical Connection CGI page (fid=168) for line stats.

    Returns upload_kbps, download_kbps, snr_upstream_db, snr_downstream_db.
    Speeds are bps literals in KseparatorAdd calls; SNR values are integer dB
    literals injected by the router firmware into the VDSL JS block.
    """
    speed_match = _PHYCONN_SPEED_RE.search(html)
    upload_kbps: int | None = None
    download_kbps: int | None = None
    if speed_match:
        upload_bps = int(speed_match.group(1))
        download_bps = int(speed_match.group(2))
        if upload_bps > 0:
            upload_kbps = upload_bps // 1000
        if download_bps > 0:
            download_kbps = download_bps // 1000

    snr_values = _PHYCONN_SNR_RE.findall(html)
    snr_upstream_db: int | None = int(snr_values[0]) if len(snr_values) >= 1 else None
    snr_downstream_db: int | None = int(snr_values[1]) if len(snr_values) >= 2 else None

    return {
        "download_kbps": download_kbps,
        "upload_kbps": upload_kbps,
        "snr_upstream_db": snr_upstream_db,
        "snr_downstream_db": snr_downstream_db,
    }


def _parse_speeds(html: str) -> dict[str, int | None]:
    """Fallback: extract downstream/upstream kbps from a DSL status .sht page."""
    text = _html.unescape(_TAG_RE.sub(" ", html))

    download_kbps: int | None = None
    upload_kbps: int | None = None

    for pattern in _DS_PATTERNS:
        match = pattern.search(text)
        if match:
            download_kbps = int(match.group(1))
            break

    for pattern in _US_PATTERNS:
        match = pattern.search(text)
        if match:
            upload_kbps = int(match.group(1))
            break

    return {"download_kbps": download_kbps, "upload_kbps": upload_kbps}


class DraytekDslCoordinator(DataUpdateCoordinator[LineData]):
    """Polls the DrayTek router for DSL line stats every minute."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        username: str,
        password: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.host = host
        self._port = port
        self._username = username
        self._password = password
        scheme = "https" if port == 443 else "http"
        self._base_url = f"{scheme}://{host}:{port}"

    async def _async_update_data(self) -> LineData:
        timeout = aiohttp.ClientTimeout(total=15)
        connector = aiohttp.TCPConnector(ssl=False)
        # unsafe=True allows cookies from IP-address hosts (routers are accessed by IP)
        cookie_jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout, cookie_jar=cookie_jar
            ) as session:
                await self._login(session)
                return await self._fetch_line_data(session)
        except aiohttp.ClientConnectorError as err:
            raise UpdateFailed(
                f"Cannot connect to router at {self._base_url}: {err}"
            ) from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Router communication error: {err}") from err

    async def _login(self, session: aiohttp.ClientSession) -> None:
        """Authenticate with the router web interface via wlogin.cgi."""
        url = (
            f"{self._base_url}/cgi-bin/wlogin.cgi"
            f"?aa={_encode_credential(self._username)}"
            f"&ab={_encode_credential(self._password)}"
        )
        async with session.get(url) as resp:
            if resp.status not in (200, 302):
                raise UpdateFailed(
                    f"Router login failed with HTTP {resp.status}. "
                    "Check your username and password."
                )

    async def _fetch_line_data(self, session: aiohttp.ClientSession) -> LineData:
        """Fetch line stats, preferring the Physical Connection page for full SNR data."""
        url = f"{self._base_url}{PHYSICAL_CONNECTION_PATH}"
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    data = _parse_physical_connection(html)
                    if data["download_kbps"] is not None or data["upload_kbps"] is not None:
                        _LOGGER.debug("Retrieved line data from Physical Connection page")
                        return data
                    _LOGGER.debug("Physical Connection page returned no speed data")
        except aiohttp.ClientError as err:
            _LOGGER.debug("Physical Connection page unavailable: %s", err)

        # Fallback: try DSL status .sht pages (speeds only, SNR will be None)
        for path in DSL_STATUS_PATHS:
            fallback_url = f"{self._base_url}{path}"
            try:
                async with session.get(fallback_url) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text()
                    speeds = _parse_speeds(html)
                    if speeds["download_kbps"] is not None or speeds["upload_kbps"] is not None:
                        _LOGGER.debug("Retrieved speed data from fallback path %s", path)
                        return {**speeds, "snr_upstream_db": None, "snr_downstream_db": None}
                    _LOGGER.debug("No speed data at fallback path %s", path)
            except aiohttp.ClientError as err:
                _LOGGER.debug("Error fetching fallback %s: %s", fallback_url, err)

        raise UpdateFailed(
            f"Could not retrieve DSL line data from {self._base_url}. "
            "The router may still be syncing, or the page paths differ for your "
            "firmware version. Enable debug logging for details."
        )

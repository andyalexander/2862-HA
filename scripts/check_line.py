"""Live test: fetch DSL line stats from the router and print them."""
from __future__ import annotations

import asyncio
import base64
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

import aiohttp

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

HOST = os.getenv("DRAYTEK_HOST")
PORT = int(os.getenv("DRAYTEK_PORT", "80"))
USERNAME = os.getenv("DRAYTEK_USERNAME", "admin")
PASSWORD = os.getenv("DRAYTEK_PASSWORD")

PHYSICAL_CONNECTION_PATH = "/cgi-bin/v2x00.cgi?sFormAuthStr=&fid=168&option=1"

import re

_SPEED_RE = re.compile(
    r"SHOWTIME</font></td>.*?KseparatorAdd\('(\d+)'\).*?KseparatorAdd\('(\d+)'\)",
    re.DOTALL | re.IGNORECASE,
)
_SNR_RE = re.compile(r'">(\d+)\s*\(dB\)</font></td>', re.IGNORECASE)


def _encode(value: str) -> str:
    return quote_plus(base64.b64encode(value.encode()).decode())


def _parse(html: str) -> dict[str, int | None]:
    sm = _SPEED_RE.search(html)
    up_bps = int(sm.group(1)) if sm else 0
    dn_bps = int(sm.group(2)) if sm else 0
    snrs = _SNR_RE.findall(html)
    return {
        "upload_kbps": up_bps // 1000 if up_bps > 0 else None,
        "download_kbps": dn_bps // 1000 if dn_bps > 0 else None,
        "snr_upstream_db": int(snrs[0]) if len(snrs) >= 1 else None,
        "snr_downstream_db": int(snrs[1]) if len(snrs) >= 2 else None,
    }


async def main() -> None:
    if not HOST or not PASSWORD:
        print("Error: set DRAYTEK_HOST and DRAYTEK_PASSWORD in .env or environment.")
        sys.exit(1)

    base = f"http://{HOST}:{PORT}"
    jar = aiohttp.CookieJar(unsafe=True)
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=False), cookie_jar=jar
    ) as session:
        login_url = (
            f"{base}/cgi-bin/wlogin.cgi"
            f"?aa={_encode(USERNAME)}&ab={_encode(PASSWORD)}"
        )
        async with session.get(login_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status not in (200, 302):
                print(f"Login failed: HTTP {r.status}")
                sys.exit(1)

        async with session.get(
            f"{base}{PHYSICAL_CONNECTION_PATH}", timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            html = await r.text()

    d = _parse(html)
    if d["download_kbps"] is None and d["upload_kbps"] is None:
        print("No data — line may not be synced.")
        sys.exit(1)

    dl = d["download_kbps"]
    ul = d["upload_kbps"]
    print(f"Download:       {dl:>7,} kbps  ({dl/1000:>7.3f} Mbit/s)")
    print(f"Upload:         {ul:>7,} kbps  ({ul/1000:>7.3f} Mbit/s)")
    print(f"SNR Upstream:   {d['snr_upstream_db']:>4} dB")
    print(f"SNR Downstream: {d['snr_downstream_db']:>4} dB")


if __name__ == "__main__":
    asyncio.run(main())

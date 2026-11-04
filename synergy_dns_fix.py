"""Work around broken local DNS for baseball.synergysportstech.com.

Import this before any Synergy event calls. Resolves Synergy hosts via
Cloudflare DNS-over-HTTPS when system DNS fails.
"""

from __future__ import annotations

import json
import socket
import urllib.request

_ORIG = socket.getaddrinfo
_CACHE: dict[str, str] = {}
_HOSTS = (
    "baseball.synergysportstech.com",
    "sport.synergysportstech.com",
)


def _doh_a(host: str) -> str | None:
    if host in _CACHE:
        return _CACHE[host]
    url = f"https://cloudflare-dns.com/dns-query?name={host}&type=A"
    req = urllib.request.Request(url, headers={"Accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=15) as response:
        doc = json.loads(response.read().decode())
    for ans in doc.get("Answer") or []:
        if int(ans.get("type") or 0) == 1 and ans.get("data"):
            _CACHE[host] = str(ans["data"]).rstrip(".")
            return _CACHE[host]
    return None


def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
    try:
        return _ORIG(host, port, family, type, proto, flags)
    except socket.gaierror:
        if host not in _HOSTS:
            raise
        ip = _doh_a(str(host))
        if not ip:
            raise
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


def install() -> None:
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    for host in _HOSTS:
        try:
            _doh_a(host)
        except Exception:
            pass


install()

import socket
import time

import requests

_TTL_S = 300
_cache: dict[str, tuple[float, list[str]]] = {}
_targets: set[str] = set()
_patched = False
_original = socket.getaddrinfo


def resolve(host: str) -> list[str]:
    now = time.time()
    hit = _cache.get(host)
    if hit and hit[0] > now:
        return hit[1]
    r = requests.get(
        "https://1.1.1.1/dns-query",
        params={"name": host, "type": "A"},
        headers={"accept": "application/dns-json"},
        timeout=10,
    )
    r.raise_for_status()
    answers = r.json().get("Answer", [])
    ips = [a["data"] for a in answers if a.get("type") == 1]
    if not ips:
        raise OSError(f"DoH: no A record for {host}")
    _cache[host] = (now + _TTL_S, ips)
    return ips


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host in _targets:
        ips = resolve(host)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))
            for ip in ips
        ]
    return _original(host, port, family, type, proto, flags)


def ensure_dns(host: str | None) -> str:
    global _patched
    if not host or host in _targets:
        return "patched" if host in _targets else "skipped"
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return "system"
    except socket.gaierror:
        _targets.add(host)
        if not _patched:
            socket.getaddrinfo = _patched_getaddrinfo
            _patched = True
        return "doh"

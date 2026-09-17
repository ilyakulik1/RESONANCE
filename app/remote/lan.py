from __future__ import annotations

import socket

from PyQt6.QtNetwork import QAbstractSocket, QNetworkInterface


def lan_ipv4_addresses() -> list[str]:
    """Return likely LAN IPv4 addresses, preferring the default-route interface."""
    found: list[str] = []
    primary = _primary_ipv4()
    if primary:
        found.append(primary)
    for address in QNetworkInterface.allAddresses():
        if address.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
            continue
        if address.isLoopback() or address.isLinkLocal():
            continue
        ip = address.toString()
        if _usable_lan_ip(ip) and ip not in found:
            found.append(ip)
    return found or ["127.0.0.1"]


def _primary_ipv4() -> str | None:
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        if _usable_lan_ip(ip):
            return ip
    except OSError:
        return None
    finally:
        if sock is not None:
            sock.close()
    return None


def _usable_lan_ip(ip: str) -> bool:
    if not ip or ip.startswith("127."):
        return False
    if ip.startswith("169.254."):
        return False
    return True


def urls_for_port(port: int) -> list[str]:
    return [f"http://{ip}:{int(port)}" for ip in lan_ipv4_addresses()]

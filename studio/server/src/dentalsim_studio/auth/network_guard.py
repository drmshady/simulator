"""Closes connections from outside the home network before any HTTP is read (FR-001, research R10, SC-011).

`GuardedH11Protocol` is uvicorn's h11 protocol with a peer-address check in `connection_made`: a peer that is not
loopback and not inside a configured home subnet (`DENTALSIM_HOME_SUBNETS`) has its transport aborted at once, with
no HTTP reply, and the protocol ignores everything that transport does afterwards. IPv4 and IPv6 peers are both
checked, and an IPv4-mapped IPv6 peer (`::ffff:a.b.c.d`) is checked as its IPv4 address.

Pass `guarded_protocol(subnets)` as `uvicorn.Config(http=...)`. Passing a class also makes uvicorn use h11 where
`uvicorn[standard]` would otherwise pick httptools.

With TLS, asyncio finishes the TLS handshake before the protocol sees the connection, so a refused peer sees the
handshake and then a closed connection, never an HTTP reply. The Windows Firewall rule and the router (no port
forwarding) are the outer layers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
from typing import ClassVar

from uvicorn.protocols.http.h11_impl import H11Protocol

from dentalsim.config import get_settings

logger = logging.getLogger(__name__)

Network = IPv4Network | IPv6Network


def is_allowed_peer(host: str | None, home_subnets: Iterable[Network]) -> bool:
    """True for a loopback address or an address inside one of `home_subnets`."""
    if not host:
        return False
    try:
        address: IPv4Address | IPv6Address = ip_address(host)
    except ValueError:
        return False
    candidates: list[IPv4Address | IPv6Address] = [address]
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        candidates.append(address.ipv4_mapped)
    if any(candidate.is_loopback for candidate in candidates):
        return True
    subnets = tuple(home_subnets)
    return any(candidate in network for candidate in candidates for network in subnets)


def peer_host(transport: asyncio.BaseTransport) -> str | None:
    """The peer's IP address as text, or None when the transport cannot say."""
    peer = transport.get_extra_info("peername")
    if peer is None:
        sock = transport.get_extra_info("socket")
        if sock is not None:
            try:
                peer = sock.getpeername()
            except OSError:
                return None
    if isinstance(peer, tuple | list) and peer:
        return str(peer[0])
    return None


class GuardedH11Protocol(H11Protocol):
    """uvicorn's h11 protocol, admitting only loopback and the home subnets."""

    home_subnets: ClassVar[tuple[Network, ...] | None] = None
    """The admitted subnets besides loopback. None reads the configured ones when a connection arrives."""

    _refused = False

    def allowed_subnets(self) -> tuple[Network, ...]:
        if self.home_subnets is not None:
            return self.home_subnets
        return tuple(get_settings().home_subnets)

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        host = peer_host(transport)
        if not is_allowed_peer(host, self.allowed_subnets()):
            self._refused = True
            logger.warning("refused a connection from %s: it is outside the home network", host or "an unknown peer")
            transport.abort()
            return
        super().connection_made(transport)

    # A refused connection never initialised the protocol, so none of the base class's handlers may run for it.

    def connection_lost(self, exc: Exception | None) -> None:
        if not self._refused:
            super().connection_lost(exc)

    def data_received(self, data: bytes) -> None:
        if not self._refused:
            super().data_received(data)

    def eof_received(self) -> None:
        if not self._refused:
            super().eof_received()

    def pause_writing(self) -> None:
        if not self._refused:
            super().pause_writing()

    def resume_writing(self) -> None:
        if not self._refused:
            super().resume_writing()


def guarded_protocol(home_subnets: Iterable[Network]) -> type[GuardedH11Protocol]:
    """A `GuardedH11Protocol` class bound to `home_subnets`, for `uvicorn.Config(http=...)`."""
    subnets = tuple(home_subnets)

    class BoundGuardedH11Protocol(GuardedH11Protocol):
        home_subnets = subnets

    return BoundGuardedH11Protocol

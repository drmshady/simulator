"""The home-network guard closes connections from outside the home subnets with no reply (T042, FR-001, SC-011)."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from ipaddress import ip_network
from typing import Any

import pytest
import uvicorn
from uvicorn.server import ServerState

from dentalsim_studio.auth import network_guard
from dentalsim_studio.auth.network_guard import GuardedH11Protocol, guarded_protocol, is_allowed_peer, peer_host

HOME = (ip_network("192.168.1.0/24"), ip_network("fd00:1::/64"))


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("127.0.0.1", True),
        ("127.5.6.7", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("192.168.1.20", True),
        ("::ffff:192.168.1.20", True),
        ("fd00:1::abcd", True),
        ("192.168.2.20", False),
        ("::ffff:192.168.2.20", False),
        ("10.0.0.5", False),
        ("8.8.8.8", False),
        ("2001:db8::1", False),
        ("fd00:2::1", False),
        ("not-an-address", False),
        ("", False),
        (None, False),
    ],
)
def test_allow_decision(host: str | None, allowed: bool) -> None:
    assert is_allowed_peer(host, HOME) is allowed


def test_only_loopback_without_home_subnets() -> None:
    assert is_allowed_peer("127.0.0.1", ())
    assert is_allowed_peer("::1", ())
    assert not is_allowed_peer("192.168.1.20", ())


class FakeTransport(asyncio.Transport):
    def __init__(self, peername: Any) -> None:
        super().__init__()
        self.peername = peername
        self.written = b""
        self.aborted = False
        self.closed = False

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return {"peername": self.peername, "sockname": ("192.168.1.10", 8443)}.get(name, default)

    def write(self, data: bytes | bytearray | memoryview) -> None:
        self.written += bytes(data)

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.aborted or self.closed

    def pause_reading(self) -> None:
        pass

    def resume_reading(self) -> None:
        pass


async def _ok_app(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.fixture
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    new_loop = asyncio.new_event_loop()
    yield new_loop
    new_loop.close()


def _protocol(
    loop: asyncio.AbstractEventLoop, subnets: tuple[Any, ...] = HOME
) -> tuple[GuardedH11Protocol, ServerState]:
    protocol_class = guarded_protocol(subnets)
    config = uvicorn.Config(_ok_app, http=protocol_class, lifespan="off", log_level="warning")
    state = ServerState()
    protocol = protocol_class(config=config, server_state=state, app_state={}, _loop=loop)
    return protocol, state


@pytest.mark.parametrize(
    "peer",
    [("192.168.1.20", 50000), ("127.0.0.1", 50000), ("::1", 50000, 0, 0), ("::ffff:192.168.1.20", 50000, 0, 0)],
)
def test_protocol_admits_home_and_loopback_peers(peer: tuple[Any, ...], loop: asyncio.AbstractEventLoop) -> None:
    protocol, state = _protocol(loop)
    transport = FakeTransport(peer)
    protocol.connection_made(transport)
    assert not transport.aborted
    assert protocol in state.connections


@pytest.mark.parametrize("peer", [("203.0.113.9", 50000), ("192.168.2.20", 50000), ("2001:db8::1", 50000, 0, 0), None])
def test_protocol_closes_outside_peers_with_no_reply(
    peer: tuple[Any, ...] | None, loop: asyncio.AbstractEventLoop
) -> None:
    protocol, state = _protocol(loop)
    transport = FakeTransport(peer)
    protocol.connection_made(transport)
    assert transport.aborted
    assert protocol not in state.connections
    # Whatever the transport does next is ignored, and nothing is ever written.
    protocol.data_received(b"GET / HTTP/1.1\r\nHost: studio\r\n\r\n")
    protocol.eof_received()
    protocol.pause_writing()
    protocol.resume_writing()
    protocol.connection_lost(None)
    assert transport.written == b""


def test_unbound_class_reads_the_configured_subnets(
    monkeypatch: pytest.MonkeyPatch, loop: asyncio.AbstractEventLoop
) -> None:
    from dentalsim.config import get_settings

    monkeypatch.setenv("DENTALSIM_HOME_SUBNETS", "10.1.0.0/16")
    get_settings.cache_clear()
    try:
        config = uvicorn.Config(_ok_app, http=GuardedH11Protocol, lifespan="off")
        protocol = GuardedH11Protocol(config=config, server_state=ServerState(), app_state={}, _loop=loop)
        assert protocol.allowed_subnets() == (ip_network("10.1.0.0/16"),)
    finally:
        get_settings.cache_clear()


def test_peer_host_falls_back_to_the_socket() -> None:
    class SocketOnly(FakeTransport):
        def get_extra_info(self, name: str, default: Any = None) -> Any:
            if name == "socket":
                return type("Sock", (), {"getpeername": lambda self: ("192.168.1.30", 1234)})()
            return default

    assert peer_host(SocketOnly(None)) == "192.168.1.30"


# A real server on loopback ------------------------------------------------------------------------------------------


@pytest.fixture
def live_server() -> Iterator[int]:
    """uvicorn with the guard on 127.0.0.1, a free port, in a thread; yields the port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(_ok_app, http=guarded_protocol(()), lifespan="off", log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "the server did not start"
        time.sleep(0.02)
    yield port
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()


def _exchange(port: int) -> bytes:
    received = b""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
        client.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        try:
            while chunk := client.recv(4096):
                received += chunk
        except (ConnectionResetError, ConnectionAbortedError):
            pass
    return received


def test_real_connection_from_loopback_is_served(live_server: int) -> None:
    reply = _exchange(live_server)
    assert reply.startswith(b"HTTP/1.1 200")
    assert b"\r\nok\r\n" in reply


def test_real_refused_connection_gets_no_bytes(live_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    decisions: list[str | None] = []

    def refuse_loopback(host: str | None, home_subnets: Any) -> bool:
        decisions.append(host)
        return False

    monkeypatch.setattr(network_guard, "is_allowed_peer", refuse_loopback)
    assert _exchange(live_server) == b""
    assert decisions == ["127.0.0.1"]

"""`dentalsim studio serve`, `studio set-password` and the worker supervisor (T047, FR-001, research R9, R10)."""

from __future__ import annotations

import io
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from ipaddress import ip_network
from pathlib import Path
from typing import Any

import httpx
import psutil
import pytest
import uvicorn

from dentalsim import cli
from dentalsim.commands import studio as studio_command
from dentalsim.config import get_settings
from dentalsim_studio import serve as serve_module
from dentalsim_studio.app import create_app
from dentalsim_studio.auth.certificate import ensure_certificates
from dentalsim_studio.auth.network_guard import GuardedH11Protocol
from dentalsim_studio.auth.password import password_is_set, set_password, verify_password
from dentalsim_studio.db import open_repos
from dentalsim_studio.serve import (
    StudioServer,
    WorkerSupervisor,
    choose_host,
    restart_delay,
    start_process,
    stop_worker,
    terminate_tree,
)

PASSWORD = "correct horse battery"


def run_cli(*argv: str) -> int:
    return cli.main(list(argv), modules=[studio_command])


def set_owner_password(data_dir: Path) -> None:
    repos = open_repos(data_dir)
    try:
        set_password(repos, PASSWORD)
    finally:
        repos.close()


# set-password --------------------------------------------------------------------------------------------------------


def _answers(monkeypatch: pytest.MonkeyPatch, *answers: str) -> list[str]:
    prompts: list[str] = []
    replies = iter(answers)

    def fake_getpass(prompt: str = "") -> str:
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(studio_command.getpass, "getpass", fake_getpass)
    return prompts


def test_set_password_prompts_twice_and_stores_the_hash(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prompts = _answers(monkeypatch, PASSWORD, PASSWORD)
    assert run_cli("studio", "set-password") == 0
    assert len(prompts) == 2
    assert "owner password is set" in capsys.readouterr().out
    repos = open_repos(data_dir)
    try:
        assert verify_password(repos, PASSWORD)
    finally:
        repos.close()


def test_set_password_refuses_different_answers(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _answers(monkeypatch, PASSWORD, PASSWORD + "!")
    assert run_cli("studio", "set-password") == 1
    assert "The two passwords differ" in capsys.readouterr().err
    repos = open_repos(data_dir)
    try:
        assert not password_is_set(repos)
    finally:
        repos.close()


def test_set_password_refuses_a_short_password(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _answers(monkeypatch, "short", "short")
    assert run_cli("studio", "set-password") == 1
    assert "at least 10 characters" in capsys.readouterr().err


def test_set_password_from_stdin(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\r\n"))
    _answers(monkeypatch)  # getpass must not be called
    assert run_cli("studio", "set-password", "--password-stdin", "--json") == 0
    repos = open_repos(data_dir)
    try:
        assert verify_password(repos, PASSWORD)
    finally:
        repos.close()


# serve ---------------------------------------------------------------------------------------------------------------


def test_serve_refuses_without_a_password(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli("studio", "serve") == 1
    assert "Set the owner password first: uv run dentalsim studio set-password" in capsys.readouterr().err
    assert list((data_dir / "certs").iterdir()) == []  # refused before anything else was made
    assert (data_dir / "studio.db").is_file()  # the migrations ran


class FakeSupervisor:
    instances: list[FakeSupervisor] = []

    def __init__(self, command: Sequence[str], **options: Any) -> None:
        self.command = list(command)
        self.events: list[str] = []
        FakeSupervisor.instances.append(self)

    def start(self) -> None:
        self.events.append("start")

    def stop_restarting(self) -> None:
        self.events.append("stop_restarting")

    def stop(self) -> None:
        self.events.append("stop")


@pytest.fixture
def mocked_server(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[uvicorn.Config, Callable[[], None]]]:
    """Owner password set; uvicorn and the worker supervisor replaced. Returns the configs uvicorn was given."""
    set_owner_password(data_dir)
    runs: list[tuple[uvicorn.Config, Callable[[], None]]] = []
    FakeSupervisor.instances = []
    monkeypatch.setattr(serve_module, "run_server", lambda config, on_exit: runs.append((config, on_exit)))
    monkeypatch.setattr(serve_module, "WorkerSupervisor", FakeSupervisor)
    return runs


def test_serve_starts_tls_uvicorn_with_the_guard_and_the_worker(
    data_dir: Path, mocked_server: list[tuple[uvicorn.Config, Callable[[], None]]]
) -> None:
    out = io.StringIO()
    serve_module.serve(out=out)
    [(config, on_exit)] = mocked_server
    assert config.ssl_certfile == str(data_dir / "certs" / "server.pem")
    assert config.ssl_keyfile == str(data_dir / "certs" / "server-key.pem")
    assert isinstance(config.http, type) and issubclass(config.http, GuardedH11Protocol)
    assert config.http.home_subnets == ()
    assert (config.host, config.port) == ("127.0.0.1", 8443)
    assert config.proxy_headers is False
    assert config.timeout_graceful_shutdown == serve_module.GRACEFUL_SHUTDOWN_S

    [supervisor] = FakeSupervisor.instances
    assert supervisor.command == [sys.executable, "-m", "dentalsim_studio.worker.main"]
    assert supervisor.events == ["start", "stop"]
    on_exit()
    assert supervisor.events[-1] == "stop_restarting"

    text = out.getvalue()
    assert "https://localhost:8443" in text
    assert "DENTALSIM_HOME_SUBNETS is not set" in text
    assert "certutil -addstore -user Root" in text
    assert "New-NetFirewallRule" in text and "-LocalPort 8443" in text
    assert text.rstrip().endswith("The studio has stopped.")


def test_second_start_prints_the_firewall_rule_but_not_the_trust_steps(
    data_dir: Path, mocked_server: list[tuple[uvicorn.Config, Callable[[], None]]]
) -> None:
    serve_module.serve(out=io.StringIO())
    out = io.StringIO()
    serve_module.serve(out=out)
    assert "certutil" not in out.getvalue()
    assert "New-NetFirewallRule" in out.getvalue()


def test_serve_with_home_subnets_listens_on_every_interface(
    data_dir: Path,
    mocked_server: list[tuple[uvicorn.Config, Callable[[], None]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DENTALSIM_HOME_SUBNETS", "192.168.1.0/24")
    get_settings.cache_clear()
    out = io.StringIO()
    serve_module.serve(port=9443, out=out)
    [(config, _)] = mocked_server
    assert (config.host, config.port) == ("0.0.0.0", 9443)
    assert isinstance(config.http, type) and issubclass(config.http, GuardedH11Protocol)
    assert config.http.home_subnets == (ip_network("192.168.1.0/24"),)
    assert "home subnets are configured (192.168.1.0/24)" in out.getvalue()


def test_cli_serve_passes_host_and_port(
    data_dir: Path, mocked_server: list[tuple[uvicorn.Config, Callable[[], None]]]
) -> None:
    assert run_cli("studio", "serve", "--host", "127.0.0.1", "--port", "9555") == 0
    [(config, _)] = mocked_server
    assert (config.host, config.port) == ("127.0.0.1", 9555)


def test_ctrl_c_still_stops_the_worker(data_dir: Path, mocked_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(config: uvicorn.Config, on_exit: Callable[[], None]) -> None:
        on_exit()
        raise KeyboardInterrupt

    monkeypatch.setattr(serve_module, "run_server", interrupted)
    serve_module.serve(out=io.StringIO())
    assert FakeSupervisor.instances[0].events == ["start", "stop_restarting", "stop"]


def test_choose_host() -> None:
    assert choose_host("192.168.1.20", ())[0] == "192.168.1.20"
    assert choose_host(None, ())[0] == "127.0.0.1"
    assert choose_host(None, (ip_network("192.168.1.0/24"),))[0] == "0.0.0.0"


def test_studio_server_tells_the_supervisor_on_a_stop_signal() -> None:
    calls: list[str] = []
    server = StudioServer(uvicorn.Config(lambda scope, receive, send: None), lambda: calls.append("exit"))
    server.handle_exit(signal.SIGINT, None)
    assert calls == ["exit"]
    assert server.should_exit


# The worker supervisor -----------------------------------------------------------------------------------------------


class FakeProcess:
    def __init__(self, pid: int, *, lives_for_polls: int | None) -> None:
        self.pid = pid
        self.lives_for_polls = lives_for_polls
        self.polls = 0
        self.returncode: int | None = None

    def poll(self) -> int | None:
        self.polls += 1
        if self.returncode is None and self.lives_for_polls is not None and self.polls > self.lives_for_polls:
            self.returncode = 3
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0

    def send_signal(self, sig: int) -> None:
        self.returncode = 0

    def terminate(self) -> None:
        self.returncode = 1

    def kill(self) -> None:
        self.returncode = 1


def _wait_for(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def test_restart_delay() -> None:
    assert restart_delay(started_at=100.0, now=101.0, interval_s=5.0) == 4.0
    assert restart_delay(started_at=100.0, now=107.0, interval_s=5.0) == 0.0


def test_worker_that_exits_is_restarted_with_a_back_off() -> None:
    started: list[float] = []
    processes: list[FakeProcess] = []

    def start(command: Sequence[str]) -> FakeProcess:
        started.append(time.monotonic())
        processes.append(FakeProcess(len(processes) + 1, lives_for_polls=1))
        return processes[-1]

    stopped: list[FakeProcess] = []
    supervisor = WorkerSupervisor(
        ["worker"], restart_interval_s=0.1, poll_interval_s=0.001, start=start, stop_process=stopped.append
    )
    supervisor.start()
    _wait_for(lambda: len(started) >= 3)
    supervisor.stop()
    count = len(started)
    time.sleep(0.25)
    assert len(started) == count  # no restart after stop
    gaps = [later - earlier for earlier, later in zip(started, started[1:], strict=False)]
    assert all(gap >= 0.09 for gap in gaps), gaps
    assert supervisor.starts == count


def test_stop_ends_a_running_worker_and_does_not_restart_it() -> None:
    processes: list[FakeProcess] = []

    def start(command: Sequence[str]) -> FakeProcess:
        processes.append(FakeProcess(42, lives_for_polls=None))
        return processes[-1]

    stopped: list[Any] = []

    def stop_tree(process: Any) -> None:
        stopped.append(process)
        process.terminate()

    supervisor = WorkerSupervisor(
        ["worker"], restart_interval_s=0.0, poll_interval_s=0.001, start=start, stop_process=stop_tree
    )
    supervisor.start()
    _wait_for(lambda: len(processes) == 1)
    supervisor.stop()
    assert stopped == processes
    time.sleep(0.05)
    assert len(processes) == 1


def test_no_restart_once_the_server_is_stopping() -> None:
    process = FakeProcess(7, lives_for_polls=None)
    starts: list[FakeProcess] = []

    def start(command: Sequence[str]) -> FakeProcess:
        starts.append(process)
        return process

    supervisor = WorkerSupervisor(["worker"], restart_interval_s=0.0, poll_interval_s=0.001, start=start)
    supervisor.start()
    _wait_for(lambda: len(starts) == 1)
    supervisor.stop_restarting()
    process.returncode = 0  # the worker exits while the server shuts down
    time.sleep(0.05)
    assert len(starts) == 1
    supervisor.stop()


def test_a_worker_that_cannot_start_is_retried() -> None:
    attempts: list[float] = []

    def start(command: Sequence[str]) -> FakeProcess:
        attempts.append(time.monotonic())
        raise OSError("no such file")

    supervisor = WorkerSupervisor(["missing"], restart_interval_s=0.02, poll_interval_s=0.001, start=start)
    supervisor.start()
    _wait_for(lambda: len(attempts) >= 2)
    supervisor.stop()
    assert supervisor.starts == 0


class StubbornProcess(FakeProcess):
    """Takes the stop signal but never exits by itself."""

    def __init__(self, *, signal_fails: bool = False) -> None:
        super().__init__(9, lives_for_polls=None)
        self.signals: list[int] = []
        self.signal_fails = signal_fails

    def send_signal(self, sig: int) -> None:
        if self.signal_fails:
            raise OSError("no console")
        self.signals.append(sig)

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("worker", timeout or 0)
        return self.returncode


def test_stop_worker_asks_with_ctrl_break_first(monkeypatch: pytest.MonkeyPatch) -> None:
    terminated: list[Any] = []
    monkeypatch.setattr(serve_module, "terminate_tree", terminated.append)
    polite = StubbornProcess()
    polite.wait = lambda timeout=None: 0  # type: ignore[method-assign]
    stop_worker(polite, timeout_s=1)
    expected = signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGTERM
    assert polite.signals == [expected]
    assert terminated == []


@pytest.mark.parametrize("signal_fails", [False, True])
def test_stop_worker_terminates_a_worker_that_does_not_stop(
    monkeypatch: pytest.MonkeyPatch, signal_fails: bool
) -> None:
    terminated: list[Any] = []
    monkeypatch.setattr(serve_module, "terminate_tree", terminated.append)
    stubborn = StubbornProcess(signal_fails=signal_fails)
    stop_worker(stubborn, timeout_s=0.01)
    assert terminated == [stubborn]


def _process_tree(tmp_path: Path, *, ignore_stop: bool) -> tuple[subprocess.Popen[bytes], list[psutil.Process]]:
    """A stand-in worker started like the real one, with one child standing in for a solver."""
    marks = [tmp_path / "worker-ready", tmp_path / "solver-ready"]
    ignore = "signal.signal(getattr(signal, 'SIGBREAK', signal.SIGTERM), signal.SIG_IGN); " if ignore_stop else ""
    solver = f"import pathlib, signal, time; {ignore}pathlib.Path({str(marks[1])!r}).write_text('y'); time.sleep(60)"
    worker = (
        f"import pathlib, signal, subprocess, sys, time; {ignore}"
        f"subprocess.Popen([sys.executable, '-c', {solver!r}]); "
        f"pathlib.Path({str(marks[0])!r}).write_text('y'); time.sleep(60)"
    )
    process = start_process([sys.executable, "-c", worker])
    assert isinstance(process, subprocess.Popen)
    _wait_for(lambda: all(mark.exists() for mark in marks), timeout=30)
    return process, psutil.Process(process.pid).children(recursive=True)


@pytest.mark.parametrize("ignore_stop", [False, True])
def test_stopping_a_real_worker_leaves_no_process_behind(tmp_path: Path, ignore_stop: bool) -> None:
    process, children = _process_tree(tmp_path, ignore_stop=ignore_stop)
    try:
        assert children
        stop_worker(process, timeout_s=0.5 if ignore_stop else 20)
        assert process.poll() is not None
        _, alive = psutil.wait_procs(children, timeout=10)
        assert alive == []
    finally:
        if process.poll() is None:
            terminate_tree(process)


def test_worker_command_runs_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    from dentalsim_studio.worker import main as worker_main

    called: list[str] = []
    monkeypatch.setattr(worker_main, "run_worker", lambda: called.append("run"))
    assert run_cli("worker") == 0
    assert called == ["run"]


# The real server -----------------------------------------------------------------------------------------------------


def test_studio_answers_over_tls_with_the_generated_certificate(data_dir: Path) -> None:
    set_owner_password(data_dir)
    certs = ensure_certificates(data_dir, ())
    config = serve_module.uvicorn_config(create_app(data_dir=data_dir), "127.0.0.1", 0, certs.paths, ())
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        _wait_for(lambda: server.started, timeout=20)
        trusted = ssl.create_default_context(cafile=str(certs.paths.ca_cert))
        with httpx.Client(verify=trusted, base_url=f"https://localhost:{port}") as client:
            anonymous = client.get("/api/auth/session")
            assert anonymous.status_code == 401
            assert anonymous.json()["code"] == "not_signed_in"
            assert "server" not in anonymous.headers
            assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
            assert client.get("/api/auth/session").json()["signed_in"] is True
        with pytest.raises(httpx.ConnectError), httpx.Client(base_url=f"https://localhost:{port}") as untrusted:
            untrusted.get("/api/auth/session")  # without the studio's CA the certificate is not trusted
    finally:
        server.should_exit = True
        thread.join(timeout=20)
        sock.close()

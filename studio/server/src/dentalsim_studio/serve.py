"""`dentalsim studio serve`: the studio server and its worker on the main PC (FR-001, FR-002, research R9, R10).

`serve()`:

1. creates the data folder and opens the database, which runs the migrations;
2. refuses to start while no owner password is set (FR-001);
3. makes or renews the HTTPS certificate;
4. prints the studio's addresses, the trust steps when a new local CA was made, and the firewall rule;
5. starts the worker as its own process (`python -m dentalsim_studio.worker.main`, in its own process group),
   restarting it when it exits, at most once every 5 s;
6. runs uvicorn with TLS and the home-network guard until stopped, then stops the worker with Ctrl+Break (SIGTERM
   off Windows), terminating it and its solver processes only if it does not exit in time.

Without `--host` the studio listens on every interface (`0.0.0.0`) only when home subnets are configured;
otherwise it listens on 127.0.0.1, since the guard would refuse every other computer anyway.
"""

from __future__ import annotations

import contextlib
import logging
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from ipaddress import ip_address
from types import FrameType
from typing import Protocol, TextIO

import psutil
import uvicorn
from fastapi import FastAPI

from dentalsim.config import ensure_data_folder, get_settings
from dentalsim.errors import Refused
from dentalsim.version import engine_version, is_dev_build
from dentalsim_studio.app import create_app
from dentalsim_studio.auth.certificate import (
    CertificatePaths,
    CertificateStatus,
    Network,
    ensure_certificates,
    firewall_command,
    home_addresses,
    trust_instructions,
)
from dentalsim_studio.auth.network_guard import guarded_protocol
from dentalsim_studio.auth.password import SET_PASSWORD_COMMAND, password_is_set
from dentalsim_studio.db import open_repos

logger = logging.getLogger(__name__)

NO_PASSWORD = f"Set the owner password first: {SET_PASSWORD_COMMAND}"
WORKER_MODULE = "dentalsim_studio.worker.main"
RESTART_INTERVAL_S = 5.0
"""The worker is started at most once in this many seconds."""
GRACEFUL_SHUTDOWN_S = 5
"""Open connections (event streams) get this long to finish when the server stops."""
WORKER_STOP_TIMEOUT_S = 30.0
"""After the stop signal, the worker gets this long to end its solver runs and exit before it is terminated."""
WORKER_KILL_TIMEOUT_S = 10.0


class WorkerProcess(Protocol):
    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def send_signal(self, sig: int) -> None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


def worker_command() -> list[str]:
    return [sys.executable, "-m", WORKER_MODULE]


def start_process(command: Sequence[str]) -> WorkerProcess:
    """Start the worker in its own process group: the console's Ctrl+C reaches only the server, which then stops the
    worker itself with Ctrl+Break."""
    if sys.platform == "win32":
        return subprocess.Popen(list(command), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    return subprocess.Popen(list(command), start_new_session=True)


def request_stop(process: WorkerProcess) -> bool:
    """Ask the worker to stop (Ctrl+Break on Windows, SIGTERM elsewhere); False when the signal cannot be sent."""
    try:
        if sys.platform == "win32":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGTERM)
    except OSError as exc:
        logger.warning("could not ask the worker to stop: %s", exc)
        return False
    return True


def stop_worker(process: WorkerProcess, timeout_s: float = WORKER_STOP_TIMEOUT_S) -> None:
    """Ask the worker to stop and wait for it.

    The worker ends its running solver process trees and leaves those jobs running, so its next start requeues them
    at the front (FR-056). A worker that does not exit in time is terminated together with every process it started,
    so no solver keeps running on its own.
    """
    if request_stop(process):
        try:
            process.wait(timeout_s)
            return
        except subprocess.TimeoutExpired:
            logger.warning("the worker did not stop within %g s: ending it and its solver processes", timeout_s)
    terminate_tree(process)


def terminate_tree(process: WorkerProcess, timeout_s: float = WORKER_KILL_TIMEOUT_S) -> None:
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:
        children = []
    process.terminate()
    for child in children:
        with contextlib.suppress(psutil.Error):
            child.terminate()
    try:
        process.wait(timeout_s)
    except subprocess.TimeoutExpired:
        process.kill()
    _, alive = psutil.wait_procs(children, timeout=timeout_s)
    for child in alive:
        with contextlib.suppress(psutil.Error):
            child.kill()


def restart_delay(started_at: float, now: float, interval_s: float) -> float:
    """Seconds to wait before the next start, so starts are at least `interval_s` apart."""
    return max(0.0, interval_s - (now - started_at))


class WorkerSupervisor:
    """Keeps one worker process running: starts it, restarts it when it exits, and stops it."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        restart_interval_s: float = RESTART_INTERVAL_S,
        poll_interval_s: float = 0.5,
        start: Callable[[Sequence[str]], WorkerProcess] = start_process,
        stop_process: Callable[[WorkerProcess], None] = stop_worker,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.command = list(command)
        self.restart_interval_s = restart_interval_s
        self.poll_interval_s = poll_interval_s
        self._start = start
        self._stop_process = stop_process
        self._clock = clock
        self.starts = 0
        self._process: WorkerProcess | None = None
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="worker-supervisor", daemon=True)
        self._thread.start()

    def stop_restarting(self) -> None:
        """Called the moment the server is told to stop, so the worker is not restarted during the shutdown."""
        self._stopping.set()

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            logger.info("stopping the worker (pid %d)", process.pid)
            self._stop_process(process)
        if self._thread is not None:
            self._thread.join(timeout=WORKER_STOP_TIMEOUT_S)

    def _run(self) -> None:
        while True:
            started_at = self._clock()
            with self._lock:
                if self._stopping.is_set():
                    return
                try:
                    process = self._start(self.command)
                except OSError as exc:
                    logger.error("could not start the worker: %s", exc)
                    process = None
                self._process = process
            if process is not None:
                self.starts += 1
                logger.info("worker started (pid %d)", process.pid)
                while process.poll() is None:
                    if self._stopping.wait(self.poll_interval_s):
                        return
                if self._stopping.is_set():
                    return
                logger.warning("the worker exited with code %s", process.poll())
            delay = restart_delay(started_at, self._clock(), self.restart_interval_s)
            logger.warning("restarting the worker in %.1f s", delay)
            if self._stopping.wait(delay):
                return


def choose_host(host: str | None, home_subnets: Sequence[Network]) -> tuple[str, str]:
    """The address to listen on, and why, in words for the owner."""
    if host:
        return host, f"--host {host} was given"
    if home_subnets:
        listed = ", ".join(str(subnet) for subnet in home_subnets)
        return "0.0.0.0", f"home subnets are configured ({listed}); the network guard admits only them and this PC"
    return "127.0.0.1", (
        "DENTALSIM_HOME_SUBNETS is not set, so only this PC can reach the studio; set it to the home network "
        "(for example 192.168.1.0/24) to open the studio to the laptop"
    )


def studio_addresses(host: str, port: int, home_subnets: Sequence[Network]) -> list[str]:
    try:
        bound = ip_address(host)
    except ValueError:
        return [f"https://{host}:{port}"]
    if bound.is_loopback:
        return [f"https://localhost:{port}"]
    if bound.is_unspecified:
        names = [socket.gethostname(), *(str(a) for a in home_addresses(home_subnets)), "localhost"]
    else:
        names = [socket.gethostname(), str(bound)]
    return [f"https://{name}:{port}" for name in names]


def banner(
    *,
    host: str,
    port: int,
    why: str,
    certs: CertificateStatus,
    home_subnets: Sequence[Network],
    data_dir: str,
) -> str:
    version = engine_version() + (" (development build)" if is_dev_build() else "")
    lines = [
        f"Simulation Studio {version}",
        f"Data folder: {data_dir}",
        f"Listening on {host}:{port}: {why}.",
        "Open the studio at:",
        *(f"  {address}" for address in studio_addresses(host, port, home_subnets)),
        "",
    ]
    if certs.ca_created:
        lines += ["A new local certificate authority was made.", trust_instructions(certs.paths, port, home_subnets)]
    else:
        if certs.server_created:
            lines.append(f"Made a new server certificate because {certs.server_reason}; ca.pem is unchanged.")
        lines += [
            "Windows Firewall rule (once, in PowerShell as administrator):",
            f"  {firewall_command(port)}",
        ]
    return "\n".join(lines)


def uvicorn_config(
    app: FastAPI, host: str, port: int, paths: CertificatePaths, home_subnets: Iterable[Network]
) -> uvicorn.Config:
    return uvicorn.Config(
        app,
        host=host,
        port=port,
        ssl_certfile=str(paths.server_cert),
        ssl_keyfile=str(paths.server_key),
        http=guarded_protocol(home_subnets),
        ws="none",
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
        log_level="info",
    )


class StudioServer(uvicorn.Server):
    """uvicorn's server, telling the worker supervisor the moment a stop signal arrives."""

    def __init__(self, config: uvicorn.Config, on_exit: Callable[[], None]) -> None:
        super().__init__(config)
        self._on_exit = on_exit

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self._on_exit()
        super().handle_exit(sig, frame)


def run_server(config: uvicorn.Config, on_exit: Callable[[], None]) -> None:
    StudioServer(config, on_exit).run()


def _configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def serve(host: str | None = None, port: int | None = None, *, out: TextIO | None = None) -> None:
    out = out or sys.stdout
    settings = get_settings()
    data_dir = ensure_data_folder(settings)
    repos = open_repos(data_dir)
    try:
        has_password = password_is_set(repos)
    finally:
        repos.close()
    if not has_password:
        raise Refused(NO_PASSWORD, code="password_not_set")

    subnets = tuple(settings.home_subnets)
    certs = ensure_certificates(data_dir, subnets)
    app = create_app(data_dir=data_dir)
    bind_host, why = choose_host(host, subnets)
    bind_port = port or settings.port
    print(
        banner(host=bind_host, port=bind_port, why=why, certs=certs, home_subnets=subnets, data_dir=str(data_dir)),
        file=out,
        flush=True,
    )

    _configure_logging()
    config = uvicorn_config(app, bind_host, bind_port, certs.paths, subnets)
    supervisor = WorkerSupervisor(worker_command())
    supervisor.start()
    try:
        run_server(config, supervisor.stop_restarting)
    except KeyboardInterrupt:
        pass
    finally:
        supervisor.stop()
    print("The studio has stopped.", file=out, flush=True)

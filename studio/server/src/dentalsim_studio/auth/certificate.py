"""The studio's HTTPS certificate (FR-001, research R10, quickstart.md section 1).

On first start the studio makes its own local certificate authority and a server certificate signed by it, both in
`<data>/certs/`:

- `ca.pem` and `ca-key.pem`: the local CA (EC P-256, 10 years). The owner trusts `ca.pem` once on each computer
  that opens the studio; `trust_instructions` prints how.
- `server.pem` and `server-key.pem`: the server certificate (EC P-256, at most 397 days) for this PC's host name,
  `localhost`, its addresses on the home network, 127.0.0.1 and ::1, with the `serverAuth` key purpose, so Chrome
  and Edge accept it once the CA is trusted.

The private keys never leave the data folder. Each start renews the server certificate (not the CA) when it is
within 30 days of expiry or no longer names the current host name and addresses; the CA is remade only when it is
missing, unreadable or itself near expiry, and then the owner must trust it again.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
from pathlib import Path

import psutil
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from dentalsim.clock import utc_now

Network = IPv4Network | IPv6Network
Address = IPv4Address | IPv6Address

CERTS_FOLDER = "certs"
CA_VALIDITY = timedelta(days=3650)
SERVER_VALIDITY = timedelta(days=397)
RENEW_BEFORE = timedelta(days=30)
BACKDATE = timedelta(minutes=5)
LOOPBACK_ADDRESSES: tuple[Address, ...] = (ip_address("127.0.0.1"), ip_address("::1"))
FIREWALL_RULE_NAME = "DentalSim Studio"


@dataclass(frozen=True)
class CertificatePaths:
    folder: Path

    @property
    def ca_cert(self) -> Path:
        return self.folder / "ca.pem"

    @property
    def ca_key(self) -> Path:
        return self.folder / "ca-key.pem"

    @property
    def server_cert(self) -> Path:
        return self.folder / "server.pem"

    @property
    def server_key(self) -> Path:
        return self.folder / "server-key.pem"


@dataclass(frozen=True)
class CertificateStatus:
    paths: CertificatePaths
    ca_created: bool
    """True when the CA was made now: every computer must trust the new `ca.pem`."""
    server_created: bool
    server_reason: str | None
    """Why the server certificate was made now, or None when the existing one was kept."""
    dns_names: tuple[str, ...]
    ip_addresses: tuple[Address, ...]
    server_expires_at: datetime


def certificate_paths(data_dir: Path) -> CertificatePaths:
    return CertificatePaths(data_dir / CERTS_FOLDER)


def home_addresses(home_subnets: Iterable[Network]) -> list[IPv4Address]:
    """This PC's non-loopback IPv4 addresses that lie inside the home subnets."""
    subnets = tuple(home_subnets)
    found: set[IPv4Address] = set()
    for addresses in psutil.net_if_addrs().values():
        for entry in addresses:
            if entry.family != socket.AF_INET:
                continue
            try:
                address = IPv4Address(entry.address)
            except ValueError:
                continue
            if not address.is_loopback and any(address in subnet for subnet in subnets):
                found.add(address)
    return sorted(found)


def required_names(host_name: str, addresses: Iterable[Address]) -> tuple[tuple[str, ...], tuple[Address, ...]]:
    """The DNS names and IP addresses the server certificate must cover."""
    dns: list[str] = []
    for name in (host_name, "localhost"):
        if name and name.lower() not in (known.lower() for known in dns):
            dns.append(name)
    ips: list[Address] = []
    for address in (*LOOPBACK_ADDRESSES, *addresses):
        if address not in ips:
            ips.append(address)
    return tuple(dns), tuple(ips)


def _new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _write(path: Path, data: bytes, *, private: bool = False) -> None:
    """Write through a temporary file and a rename, so a crash never leaves half a certificate or key."""
    temporary = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(temporary, flags, 0o600 if private else 0o644)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(temporary, path)


def _write_key(path: Path, key: ec.EllipticCurvePrivateKey) -> None:
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    _write(path, pem, private=True)


def _load_pair(cert_path: Path, key_path: Path) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey] | None:
    """The certificate and its EC private key, or None when either is missing, unreadable or they do not match."""
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        return None
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    der = serialization.Encoding.DER
    if cert.public_key().public_bytes(der, spki) != key.public_key().public_bytes(der, spki):
        return None
    return cert, key


def _key_usage(*, ca: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=True,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=ca,
        crl_sign=ca,
        encipher_only=False,
        decipher_only=False,
    )


def make_ca(host_name: str, now: datetime) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = _new_key()
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, f"DentalSim Studio local CA ({host_name})"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DentalSim Studio"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - BACKDATE)
        .not_valid_after(now - BACKDATE + CA_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(ca=True), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return cert, key


def make_server_certificate(
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    dns_names: Sequence[str],
    ip_addresses: Sequence[Address],
    now: datetime,
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = _new_key()
    names: list[x509.GeneralName] = [x509.DNSName(name) for name in dns_names]
    names += [x509.IPAddress(address) for address in ip_addresses]
    ca_key_id = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns_names[0])]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - BACKDATE)
        .not_valid_after(now - BACKDATE + SERVER_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(ca=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_key_id), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return cert, key


def _covered(cert: x509.Certificate, dns_names: Iterable[str], ip_addresses: Iterable[Address]) -> bool:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    have_dns = {name.lower() for name in san.get_values_for_type(x509.DNSName)}
    have_ips = set(san.get_values_for_type(x509.IPAddress))
    return all(name.lower() in have_dns for name in dns_names) and all(ip in have_ips for ip in ip_addresses)


def _renew_reason(
    cert: x509.Certificate,
    ca_cert: x509.Certificate,
    dns_names: Sequence[str],
    ip_addresses: Sequence[Address],
    now: datetime,
) -> str | None:
    try:
        cert.verify_directly_issued_by(ca_cert)
    except (ValueError, TypeError, InvalidSignature):
        return "it was not signed by the current local CA"
    if cert.not_valid_after_utc - now < RENEW_BEFORE:
        return f"it expires on {cert.not_valid_after_utc:%Y-%m-%d}"
    if cert.not_valid_before_utc > now:
        return "it is not valid yet"
    if not _covered(cert, dns_names, ip_addresses):
        return "this PC's name or home-network address changed"
    return None


def ensure_certificates(
    data_dir: Path,
    home_subnets: Iterable[Network] = (),
    *,
    host_name: str | None = None,
    addresses: Iterable[Address] | None = None,
) -> CertificateStatus:
    """Make the CA and the server certificate when missing, and renew the server certificate when needed.

    `host_name` defaults to `socket.gethostname()` and `addresses` to this PC's addresses in `home_subnets`.
    """
    paths = certificate_paths(data_dir)
    paths.folder.mkdir(parents=True, exist_ok=True)
    host_name = host_name or socket.gethostname()
    dns_names, ip_addresses = required_names(
        host_name, home_addresses(home_subnets) if addresses is None else addresses
    )
    now = utc_now()

    ca_created = False
    ca_pair = _load_pair(paths.ca_cert, paths.ca_key)
    if ca_pair is None or ca_pair[0].not_valid_after_utc - now < RENEW_BEFORE:
        ca_pair = make_ca(host_name, now)
        _write_key(paths.ca_key, ca_pair[1])
        _write(paths.ca_cert, ca_pair[0].public_bytes(serialization.Encoding.PEM))
        ca_created = True
    ca_cert, ca_key = ca_pair

    server_pair = _load_pair(paths.server_cert, paths.server_key)
    reason: str | None
    if ca_created:
        reason = "a new local CA was made"
    elif server_pair is None:
        reason = "there was no usable server certificate"
    else:
        reason = _renew_reason(server_pair[0], ca_cert, dns_names, ip_addresses, now)
    if reason is not None or server_pair is None:
        server_pair = make_server_certificate(ca_cert, ca_key, dns_names, ip_addresses, now)
        _write_key(paths.server_key, server_pair[1])
        _write(paths.server_cert, server_pair[0].public_bytes(serialization.Encoding.PEM))

    return CertificateStatus(
        paths=paths,
        ca_created=ca_created,
        server_created=reason is not None,
        server_reason=reason,
        dns_names=dns_names,
        ip_addresses=ip_addresses,
        server_expires_at=server_pair[0].not_valid_after_utc,
    )


def firewall_command(port: int) -> str:
    """The Windows Firewall rule: inbound on the studio port, Private profile only, local subnet only."""
    return (
        f'New-NetFirewallRule -DisplayName "{FIREWALL_RULE_NAME}" -Direction Inbound -Protocol TCP '
        f"-LocalPort {port} -Profile Private -RemoteAddress LocalSubnet -Action Allow"
    )


def trust_instructions(paths: CertificatePaths, port: int, subnets: Iterable[Network]) -> str:
    """The once-per-computer steps to trust the local CA, and the firewall rule for this PC."""
    subnets = tuple(subnets)
    if subnets:
        admitted = "Connections are admitted from this PC and from " + ", ".join(str(s) for s in subnets) + "."
    else:
        admitted = (
            "DENTALSIM_HOME_SUBNETS is not set, so only this PC can connect. To open the studio to the laptop, set "
            "it to the home network (for example 192.168.1.0/24) and start the studio again."
        )
    return "\n".join(
        [
            "Trust the studio's certificate once on each computer that opens the studio (this PC and the laptop):",
            "  1. Copy this file to that computer, for example with a USB stick:",
            f"       {paths.ca_cert}",
            "     Copy only ca.pem. The -key.pem files stay in the data folder and never leave this PC.",
            "  2. On that computer, open PowerShell (no administrator rights needed) and run, with the path where",
            "     you put ca.pem (on this PC, the path above):",
            f'       certutil -addstore -user Root "{paths.ca_cert}"',
            "     Windows asks whether to install the certificate: choose Yes.",
            "  3. Close every Chrome or Edge window, then open the studio again.",
            "",
            "Allow the studio through Windows Firewall on this PC, only on the Private profile and only from the local",
            "subnet. Run once, in PowerShell as administrator:",
            f"  {firewall_command(port)}",
            "The home network must be set to Private in Windows settings, and the router must not forward any port",
            "to this PC.",
            "",
            admitted,
        ]
    )

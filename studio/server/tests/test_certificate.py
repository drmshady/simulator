"""The local CA and the server certificate (T044, FR-001, research R10)."""

from __future__ import annotations

from datetime import timedelta
from ipaddress import ip_address, ip_network
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID
from cryptography.x509.verification import PolicyBuilder, Store

from dentalsim.clock import utc_now
from dentalsim.testing import FixedClock
from dentalsim_studio.auth import certificate
from dentalsim_studio.auth.certificate import (
    SERVER_VALIDITY,
    CertificateStatus,
    ensure_certificates,
    firewall_command,
    trust_instructions,
)

HOME = (ip_network("192.168.1.0/24"),)
HOME_IP = ip_address("192.168.1.20")


def _load(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def _make(data_dir: Path, *, host: str = "MAIN-PC", addresses: tuple[object, ...] = (HOME_IP,)) -> CertificateStatus:
    return ensure_certificates(data_dir, HOME, host_name=host, addresses=addresses)  # type: ignore[arg-type]


def _verify(status: CertificateStatus, subject: x509.DNSName | x509.IPAddress) -> None:
    ca = _load(status.paths.ca_cert)
    server = _load(status.paths.server_cert)
    verifier = PolicyBuilder().store(Store([ca])).time(utc_now()).build_server_verifier(subject)
    chain = verifier.verify(server, [])
    assert chain[-1] == ca


def test_first_start_makes_a_ca_and_a_server_certificate(data_dir: Path, fixed_time: FixedClock) -> None:
    status = _make(data_dir)
    assert status.ca_created and status.server_created
    folder = data_dir / "certs"
    assert sorted(p.name for p in folder.iterdir()) == ["ca-key.pem", "ca.pem", "server-key.pem", "server.pem"]

    ca = _load(status.paths.ca_cert)
    constraints = ca.extensions.get_extension_for_class(x509.BasicConstraints)
    assert constraints.critical and constraints.value.ca
    usage = ca.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.key_cert_sign and usage.crl_sign
    assert ca.not_valid_after_utc - ca.not_valid_before_utc == timedelta(days=3650)
    assert isinstance(ca.public_key(), ec.EllipticCurvePublicKey)


def test_server_certificate_names_eku_and_validity(data_dir: Path, fixed_time: FixedClock) -> None:
    status = _make(data_dir)
    server = _load(status.paths.server_cert)
    san = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["MAIN-PC", "localhost"]
    assert set(san.get_values_for_type(x509.IPAddress)) == {ip_address("127.0.0.1"), ip_address("::1"), HOME_IP}
    eku = server.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(eku) == [ExtendedKeyUsageOID.SERVER_AUTH]
    assert not server.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    usage = server.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.digital_signature and not usage.key_cert_sign
    assert server.not_valid_after_utc - server.not_valid_before_utc <= timedelta(days=397)
    assert server.not_valid_after_utc - server.not_valid_before_utc == SERVER_VALIDITY


@pytest.mark.parametrize(
    "subject",
    [
        x509.DNSName("MAIN-PC"),
        x509.DNSName("localhost"),
        x509.IPAddress(ip_address("127.0.0.1")),
        x509.IPAddress(ip_address("::1")),
        x509.IPAddress(HOME_IP),
    ],
)
def test_chain_verifies_like_a_browser_would(data_dir: Path, subject: x509.DNSName | x509.IPAddress) -> None:
    _verify(_make(data_dir), subject)


def test_chain_does_not_cover_other_names(data_dir: Path) -> None:
    with pytest.raises(x509.verification.VerificationError):
        _verify(_make(data_dir), x509.DNSName("other-pc"))


def test_second_start_reuses_the_files(data_dir: Path, fixed_time: FixedClock) -> None:
    first = _make(data_dir)
    before = {p.name: p.read_bytes() for p in first.paths.folder.iterdir()}
    fixed_time.advance(days=100)
    again = _make(data_dir)
    assert not again.ca_created and not again.server_created and again.server_reason is None
    assert {p.name: p.read_bytes() for p in again.paths.folder.iterdir()} == before


def test_near_expiry_renews_the_server_certificate_only(data_dir: Path, fixed_time: FixedClock) -> None:
    first = _make(data_dir)
    ca_before = first.paths.ca_cert.read_bytes()
    server_before = first.paths.server_cert.read_bytes()
    fixed_time.advance(days=397 - 29)
    renewed = _make(data_dir)
    assert renewed.server_created and not renewed.ca_created
    assert renewed.server_reason is not None and "expires" in renewed.server_reason
    assert renewed.paths.ca_cert.read_bytes() == ca_before
    assert renewed.paths.server_cert.read_bytes() != server_before
    assert renewed.server_expires_at - utc_now() > timedelta(days=390)
    _verify(renewed, x509.DNSName("localhost"))


def test_new_address_or_host_name_renews_the_server_certificate(data_dir: Path) -> None:
    _make(data_dir)
    moved = _make(data_dir, addresses=(ip_address("192.168.1.77"),))
    assert moved.server_created and not moved.ca_created
    assert "changed" in (moved.server_reason or "")
    renamed = _make(data_dir, host="NEW-NAME", addresses=(ip_address("192.168.1.77"),))
    assert renamed.server_created
    _verify(renamed, x509.DNSName("NEW-NAME"))


def test_missing_ca_makes_a_new_ca_and_server_certificate(data_dir: Path) -> None:
    first = _make(data_dir)
    first.paths.ca_key.unlink()
    again = _make(data_dir)
    assert again.ca_created and again.server_created
    _verify(again, x509.DNSName("localhost"))


def test_home_addresses_come_from_the_interfaces_inside_the_subnets(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket
    from types import SimpleNamespace

    interfaces = {
        "Ethernet": [
            SimpleNamespace(family=socket.AF_INET, address="192.168.1.20"),
            SimpleNamespace(family=socket.AF_INET6, address="fe80::1"),
        ],
        "Wi-Fi": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.5")],
        "Loopback": [SimpleNamespace(family=socket.AF_INET, address="127.0.0.1")],
    }
    monkeypatch.setattr(certificate.psutil, "net_if_addrs", lambda: interfaces)
    assert certificate.home_addresses(HOME) == [HOME_IP]
    assert certificate.home_addresses(()) == []


def test_trust_instructions_and_firewall_command(data_dir: Path) -> None:
    status = _make(data_dir)
    text = trust_instructions(status.paths, 8443, HOME)
    assert f'certutil -addstore -user Root "{status.paths.ca_cert}"' in text
    assert "-key.pem files stay in the data folder" in text
    assert "192.168.1.0/24" in text
    expected = (
        'New-NetFirewallRule -DisplayName "DentalSim Studio" -Direction Inbound -Protocol TCP -LocalPort 8443 '
        "-Profile Private -RemoteAddress LocalSubnet -Action Allow"
    )
    assert firewall_command(8443) == expected
    assert expected in text
    assert "DENTALSIM_HOME_SUBNETS is not set" in trust_instructions(status.paths, 8443, ())

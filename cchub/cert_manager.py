"""Generate and install a local root CA + 127.0.0.1 server cert.

The root CA is installed into the current user's "Root" cert store via certutil,
so Chrome (and therefore Tampermonkey's GM_xmlhttpRequest) trusts
https://127.0.0.1:5000 without warnings.
"""
import datetime as dt
import ipaddress
import subprocess
from pathlib import Path
from typing import Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from . import paths

CA_KEY = "ca.key.pem"
CA_CERT = "ca.cert.pem"
SERVER_KEY = "server.key.pem"
SERVER_CERT = "server.cert.pem"

_CA_SUBJECT = x509.Name([
    x509.NameAttribute(NameOID.COMMON_NAME, "Case Clicker Hub Local CA"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "CCHub"),
])


def _write_pem(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _generate_ca() -> Tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_CA_SUBJECT)
        .issuer_name(_CA_SUBJECT)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365 * 10))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _build_san_list(extra_hosts: Tuple[str, ...] = ()) -> list:
    entries = [
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        x509.IPAddress(ipaddress.IPv6Address("::1")),
        x509.DNSName("localhost"),
    ]
    seen_ips: set = {"127.0.0.1", "::1"}
    seen_dns: set = {"localhost"}
    for host in extra_hosts:
        host = (host or "").strip()
        if not host:
            continue
        try:
            ip = ipaddress.ip_address(host)
            key = str(ip)
            if key in seen_ips:
                continue
            entries.append(x509.IPAddress(ip))
            seen_ips.add(key)
        except ValueError:
            key = host.lower()
            if key in seen_dns:
                continue
            entries.append(x509.DNSName(host))
            seen_dns.add(key)
    return entries


def _generate_server_cert(
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    extra_hosts: Tuple[str, ...] = (),
) -> Tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    san = x509.SubjectAlternativeName(_build_san_list(extra_hosts))
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365 * 5))
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _cert_san_set(cert: x509.Certificate) -> set:
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return set()
    out: set = set()
    for ip in ext.get_values_for_type(x509.IPAddress):
        out.add(str(ip))
    for dns in ext.get_values_for_type(x509.DNSName):
        out.add(dns.lower())
    return out


def _expected_san_set(extra_hosts: Tuple[str, ...]) -> set:
    out: set = {"127.0.0.1", "::1", "localhost"}
    for host in extra_hosts:
        host = (host or "").strip()
        if not host:
            continue
        try:
            out.add(str(ipaddress.ip_address(host)))
        except ValueError:
            out.add(host.lower())
    return out


def ensure_certs(extra_hosts: Tuple[str, ...] = ()) -> Tuple[Path, Path]:
    """Generate CA + server cert if missing. Regenerate server cert when SANs change.
    Returns (cert_path, key_path)."""
    paths.ensure_dirs()
    cert_dir = paths.CERT_DIR
    ca_key_path = cert_dir / CA_KEY
    ca_cert_path = cert_dir / CA_CERT
    server_key_path = cert_dir / SERVER_KEY
    server_cert_path = cert_dir / SERVER_CERT

    if not ca_key_path.exists() or not ca_cert_path.exists():
        ca_key, ca_cert = _generate_ca()
        _write_pem(
            ca_key_path,
            ca_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ),
        )
        _write_pem(ca_cert_path, ca_cert.public_bytes(serialization.Encoding.PEM))
    else:
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())

    expected = _expected_san_set(extra_hosts)
    regenerate = not server_key_path.exists() or not server_cert_path.exists()
    if not regenerate:
        try:
            existing = x509.load_pem_x509_certificate(server_cert_path.read_bytes())
            if not expected.issubset(_cert_san_set(existing)):
                regenerate = True
        except (OSError, ValueError):
            regenerate = True

    if regenerate:
        server_key, server_cert = _generate_server_cert(ca_key, ca_cert, extra_hosts)
        _write_pem(
            server_key_path,
            server_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ),
        )
        _write_pem(server_cert_path, server_cert.public_bytes(serialization.Encoding.PEM))

    return server_cert_path, server_key_path


def install_ca_to_windows_store() -> bool:
    """Install the CA into the current user's Root store via certutil.

    Returns True if newly installed or already trusted, False if install failed.
    Safe to call on every startup — certutil is a no-op if the cert is already
    present.
    """
    ca_cert_path = paths.CERT_DIR / CA_CERT
    if not ca_cert_path.exists():
        return False
    try:
        result = subprocess.run(
            ["certutil", "-user", "-addstore", "Root", str(ca_cert_path)],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def uninstall_ca_from_windows_store() -> bool:
    """Remove the CA from the user's Root store (used by uninstaller)."""
    try:
        result = subprocess.run(
            ["certutil", "-user", "-delstore", "Root", "Case Clicker Hub Local CA"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

#!/usr/bin/env python3
"""Smoke test an installed BoringSSL-linked cryptography wheel."""

from __future__ import annotations

import datetime
import sys

from cryptography import x509
from cryptography.hazmat.backends.openssl.backend import backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID


def main() -> int:
    loaded = backend.openssl_version_text()
    linked = backend._ffi.string(backend._lib.OPENSSL_VERSION_TEXT).decode("ascii")
    print(f"cryptography loaded: {loaded}")
    print(f"cryptography linked: {linked}")

    if "BoringSSL" not in loaded or "BoringSSL" not in linked:
        print(
            f"ERROR: Expected BoringSSL backend, got loaded={loaded!r}, linked={linked!r}",
            file=sys.stderr,
        )
        return 1

    # 1. Symmetric AEAD (AES-256-GCM)
    key = AESGCM.generate_key(bit_length=256)
    aesgcm = AESGCM(key)
    nonce = b"\x00" * 12
    ct = aesgcm.encrypt(nonce, b"boringssl-wheel-test", b"aad")
    pt = aesgcm.decrypt(nonce, ct, b"aad")
    assert pt == b"boringssl-wheel-test", "AES-GCM roundtrip failed"

    # 2. Ed25519 sign/verify
    ed_priv = ed25519.Ed25519PrivateKey.generate()
    ed_sig = ed_priv.sign(b"test-message")
    ed_priv.public_key().verify(ed_sig, b"test-message")

    # 3. ECDSA P-256 sign/verify
    ec_priv = ec.generate_private_key(ec.SECP256R1())
    ec_sig = ec_priv.sign(b"test-message", ec.ECDSA(hashes.SHA256()))
    ec_priv.public_key().verify(ec_sig, b"test-message", ec.ECDSA(hashes.SHA256()))

    # 4. RSA-2048 sign/verify + X.509 self-signed cert
    rsa_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_sig = rsa_priv.sign(
        b"test-message",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    rsa_priv.public_key().verify(
        rsa_sig,
        b"test-message",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )

    now = datetime.datetime.now(datetime.timezone.utc)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cryptography-boringssl-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(ed_priv.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(ed_priv, None)
    )
    pem_bytes = cert.public_bytes(serialization.Encoding.PEM)
    assert b"BEGIN CERTIFICATE" in pem_bytes

    print("All cryptography + BoringSSL smoketests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

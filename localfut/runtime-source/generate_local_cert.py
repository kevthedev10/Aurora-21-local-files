from pathlib import Path
import datetime
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parent
CERTS = ROOT / "certs"
CERTS.mkdir(exist_ok=True)

now = datetime.datetime.now(datetime.timezone.utc)


def make_ca(common_name, org, ou, email):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Redwood City"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, ou),
        x509.NameAttribute(NameOID.EMAIL_ADDRESS, email),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert, name


def make_leaf(ca_key, ca_name, common_name):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Electronic Arts, Inc."),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Global Online Studio"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("winter15.gosredirector.ea.com"),
                x509.DNSName("spring18.gosredirector.ea.com"),
            ]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def key_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


def cert_pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def write_modulus(name, ca_key):
    n = ca_key.public_key().public_numbers().n
    (ROOT / name).write_bytes(n.to_bytes(256, "big"))


# ---- GOS 2015 Certificate Authority (classic redirector trust root) ----
ca15_key, ca15_cert, ca15_name = make_ca(
    "GOS 2015 Certificate Authority",
    "Electronic Arts, Inc.",
    "Global Online Studio",
    "GOSDirtysockSupport@ea.com",
)
leaf15_key, leaf15_cert = make_leaf(ca15_key, ca15_name, "winter15.gosredirector.ea.com")

(CERTS / "local-gos2015-ca-key.pem").write_bytes(key_pem(ca15_key))
(CERTS / "local-gos2015-ca-cert.pem").write_bytes(cert_pem(ca15_cert))
(CERTS / "spring18-key.pem").write_bytes(key_pem(leaf15_key))
(CERTS / "spring18-cert.pem").write_bytes(cert_pem(leaf15_cert))
(CERTS / "spring18-chain.pem").write_bytes(cert_pem(leaf15_cert) + cert_pem(ca15_cert))
write_modulus("local-gos2015-modulus.bin", ca15_key)

# ---- Gameplay Services 2019 Certificate Authority (secondary root) ----
ca19_key, ca19_cert, ca19_name = make_ca(
    "Gameplay Services 2019 Certificate Authority",
    "Electronic Arts, Inc.",
    "Gameplay Services",
    "GOSDirtysockSupport@ea.com",
)
leaf19_key, leaf19_cert = make_leaf(ca19_key, ca19_name, "spring18.gosredirector.ea.com")

(CERTS / "local-gs2019-ca-key.pem").write_bytes(key_pem(ca19_key))
(CERTS / "local-gs2019-ca-cert.pem").write_bytes(cert_pem(ca19_cert))
(CERTS / "spring18-gs2019-key.pem").write_bytes(key_pem(leaf19_key))
(CERTS / "spring18-gs2019-cert.pem").write_bytes(cert_pem(leaf19_cert))
(CERTS / "spring18-gs2019-chain.pem").write_bytes(
    cert_pem(leaf19_cert) + cert_pem(ca19_cert)
)
write_modulus("local-gs2019-modulus.bin", ca19_key)

print("Created FUT 21 local CAs and redirector certificates.")
print("CA #1 subject:", ca15_cert.subject.rfc4514_string())
print("CA #2 subject:", ca19_cert.subject.rfc4514_string())
print("Leaf (GOS2015) subject:", leaf15_cert.subject.rfc4514_string())
print("Leaf (GS2019)  subject:", leaf19_cert.subject.rfc4514_string())
print("Leaf SAN: winter15.gosredirector.ea.com, spring18.gosredirector.ea.com")
print("Moduli: local-gos2015-modulus.bin, local-gs2019-modulus.bin")

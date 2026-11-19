"""Generate a local CA and server certificate for an offline LAN.
Requires cryptography only during certificate creation. Server uses stdlib ssl.
Keep lan-ca-key.pem private; distribute ONLY lan-ca.crt to trusted clients.
"""
import argparse
import datetime as dt
import ipaddress
import os
from pathlib import Path


def generate(output, ips, names):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Выберите пустую папку: существующие ключи не заменяются')
    addresses = [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    addresses += [x509.DNSName(name) for name in names]
    if not addresses:
        raise ValueError('Укажите хотя бы один IP или DNS-адрес сервера')
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'LAN Connect TAMBOV local CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-dt.timedelta(minutes=5)).not_valid_after(now+dt.timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'LAN Connect server')])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(ca_name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-dt.timedelta(minutes=5)).not_valid_after(now+dt.timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.SubjectAlternativeName(addresses), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
            .sign(ca_key, hashes.SHA256()))
    for name, item in [('lan-ca-key.pem', ca_key), ('server-key.pem', key)]:
        path = output/name
        fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(item.private_bytes(serialization.Encoding.PEM,
                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (output/'lan-ca.crt').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (output/'server-cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM)+ca.public_bytes(serialization.Encoding.PEM))
    print('CA SHA-256:', ca.fingerprint(hashes.SHA256()).hex())
    print('Created certificates:', output.resolve())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--ip', action='append', default=[])
    parser.add_argument('--dns', action='append', default=[])
    args = parser.parse_args()
    generate(args.out,args.ip,args.dns)


if __name__=='__main__':main()

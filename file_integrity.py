"""Streaming integrity checks; never modifies the file being checked."""
import hashlib
import re
import secrets


class IntegrityError(OSError):
    pass


def verify_download(path, size, expected=None):
    if path.stat().st_size != size:
        raise IntegrityError('Размер скачанного файла не совпал')
    if not expected:
        return False  # Historical metadata from pre-checksum server versions.
    if not isinstance(expected,str) or not re.fullmatch('[0-9a-fA-F]{64}',expected):
        raise IntegrityError('Некорректная контрольная сумма сервера')
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):digest.update(part)
    if not secrets.compare_digest(digest.hexdigest(),expected.lower()):
        raise IntegrityError('Контрольная сумма скачанного файла не совпала; повторите скачивание')
    return True

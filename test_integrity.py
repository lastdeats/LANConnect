from pathlib import Path
import hashlib, tempfile
from file_integrity import verify_download, IntegrityError
with tempfile.TemporaryDirectory() as td:
 p=Path(td)/'partial';data=b'file contents'*10000;p.write_bytes(data)
 digest=hashlib.sha256(data).hexdigest()
 assert verify_download(p,len(data),digest)
 assert verify_download(p,len(data),digest.upper())
 assert verify_download(p,len(data),None) is False
 for size,checksum in [(len(data)+1,digest),(len(data),'invalid')]:
  try:verify_download(p,size,checksum);raise AssertionError('invalid integrity accepted')
  except IntegrityError:pass
 p.write_bytes(b'X'+data[1:])
 try:verify_download(p,len(data),digest);raise AssertionError('same-size corruption accepted')
 except IntegrityError:pass
 assert p.read_bytes()==b'X'+data[1:] # verifier never deletes user data
 p.write_bytes(b'');assert verify_download(p,0,hashlib.sha256(b'').hexdigest())
 print('PASS: streaming checksum, same-size corruption, size mismatch, bad metadata, legacy and empty file')

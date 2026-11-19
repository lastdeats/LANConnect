"""Synthetic ZIP/manifest fixtures only: not a real APK build or signature test."""
from pathlib import Path
import hashlib, tempfile, zipfile, zlib
from apk_contract import inspect_apk
badging="""package: name='org.tambov.lanconnect' versionCode='10' versionName='0.10-dev'
sdkVersion:'26'
targetSdkVersion:'35'
launchable-activity: name='org.tambov.lanconnect.MainActivity'
"""
with tempfile.TemporaryDirectory() as td:
 p=Path(td)/'fixture.apk'
 def dexfile(version=b'038'):
  header=bytearray(112);header[:8]=b'dex\n'+version+b'\0';header[32:36]=(112).to_bytes(4,'little');header[36:40]=(112).to_bytes(4,'little');header[40:44]=(0x12345678).to_bytes(4,'little')
  header[12:32]=hashlib.sha1(header[32:]).digest();header[8:12]=(zlib.adler32(header[12:])&0xffffffff).to_bytes(4,'little');return bytes(header)
 def make(dex=None,extra=None):
  if dex is None:dex=dexfile()
  with zipfile.ZipFile(p,'w') as z:
   for name,data in {'AndroidManifest.xml':b'fixture','resources.arsc':b'fixture','classes.dex':dex,**(extra or {})}.items():z.writestr(name,data)
 def reject(text):
  try:inspect_apk(p,text);raise AssertionError('Invalid APK contract accepted')
  except ValueError:pass
 make();assert inspect_apk(p,badging)['min_sdk']==26
 reject(badging.replace("sdkVersion:'26'","sdkVersion:'28'"))
 reject(badging.replace('org.tambov.lanconnect','org.wrong.app'))
 reject(badging+"uses-permission: name='android.permission.CAMERA'\n")
 make(dex=dexfile(b'039'));reject(badging)
 make(dex=b'dex\n038\0');reject(badging)
 make(extra={'classes10.dex':dexfile(b'039')});reject(badging)
 broken=bytearray(dexfile());broken[-1]^=1;make(dex=broken);reject(badging)
 make(extra={'lib/arm64-v8a/unexpected.so':b'fixture'});reject(badging)
 with zipfile.ZipFile(p,'w') as z:z.writestr('AndroidManifest.xml',b'fixture')
 reject(badging)
 print('PASS: APK contract fixtures reject wrong package/SDK/camera/DEX/native libraries and incomplete output; no SDK compilation or installation performed')

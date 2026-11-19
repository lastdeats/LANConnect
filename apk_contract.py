"""Structural/manifest policy for built APKs; signature verification remains apksigner's job."""
from pathlib import Path
import hashlib
import re
import zlib
import zipfile

PACKAGE='org.tambov.lanconnect'


def inspect_apk(path,badging):
    package=re.search(r"^package: name='([^']+)'",badging,re.M)
    minimum=re.search(r"^sdkVersion:'(\d+)'",badging,re.M)
    target=re.search(r"^targetSdkVersion:'(\d+)'",badging,re.M)
    if not package or package[1]!=PACKAGE:raise ValueError('Unexpected APK package')
    if not minimum or minimum[1]!='26':raise ValueError('APK minSdk must remain 26 (Android 8.0)')
    if not target or target[1]!='35':raise ValueError('Unexpected target SDK')
    if not re.search(r"^launchable-activity: name='org\.tambov\.lanconnect\.MainActivity'",badging,re.M):
        raise ValueError('Main activity missing')
    if re.search(r"uses-permission[^\n]*name='android\.permission\.CAMERA'",badging):raise ValueError('Camera permission is outside project scope')
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:raise ValueError('Damaged APK archive')
        names=archive.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate ZIP entries')
        for required in ('AndroidManifest.xml','resources.arsc','classes.dex'):
            if required not in names:raise ValueError('Incomplete APK: '+required)
        if any(name.startswith('lib/') and name.endswith('.so') for name in names):
            raise ValueError('Unexpected native libraries: universal Java-only APK expected')
        dex_versions=[]
        for name in names:
            if re.fullmatch(r'classes(?:[2-9]|[1-9][0-9]+)?\.dex',name):
                with archive.open(name) as stream:header=stream.read(112)
                if len(header)!=112 or not re.fullmatch(b'dex\n0(?:35|37|38)\x00',header[:8]):
                    raise ValueError('DEX format is not compatible with Android 8.0')
                if int.from_bytes(header[32:36],'little')!=archive.getinfo(name).file_size or int.from_bytes(header[36:40],'little')!=112 or int.from_bytes(header[40:44],'little')!=0x12345678:
                    raise ValueError('Invalid DEX header size/endianness')
                digest=hashlib.sha1();checksum=1
                with archive.open(name) as stream:
                    stream.read(12);checksum=zlib.adler32(stream.read(20),checksum)
                    for part in iter(lambda:stream.read(1024*1024),b''):
                        digest.update(part);checksum=zlib.adler32(part,checksum)
                if digest.digest()!=header[12:32] or checksum&0xffffffff!=int.from_bytes(header[8:12],'little'):
                    raise ValueError('DEX content checksum mismatch')
                dex_versions.append(header[4:7].decode('ascii'))
    return {'package':PACKAGE,'min_sdk':26,'target_sdk':35,'native_libraries':False,'dex_versions':dex_versions}

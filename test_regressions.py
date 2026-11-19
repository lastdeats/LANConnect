
"""Isolated HTTP regression checks; disk capacity is simulated, not a 5 GiB transfer."""

import tempfile
import pathlib
import subprocess
import sys
import os
import socket
import time
import json
import urllib.request
import urllib.error
import sqlite3

src = pathlib.Path(__file__).parent

with tempfile.TemporaryDirectory() as td:
    root = pathlib.Path(td)

    (root / 'live.py').write_bytes(
        (src / 'live.py').read_bytes()
    )

    code = (src / 'server.py').read_text(
        encoding='utf-8'
    ).replace(
        "if __name__=='__main__':",
        "shutil.disk_usage=lambda path:type('Disk',(),{'free':8*1024**3})()\nif __name__=='__main__':"
    )

    (root / 'server.py').write_text(
        code,
        encoding='utf-8'
    )

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]

    env = dict(
        os.environ,
        LAN_HOST='127.0.0.1',
        LAN_PORT=str(port),
        LAN_MIN_FREE_GIB='1'
    )

    def start_server():
        return subprocess.Popen(
            [sys.executable, str(root / 'server.py')],
            cwd=str(root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    def stop_server(proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        else:
            proc.wait()

    def req(path, obj=None, token=None, raw=None):
        data = (
            raw if raw is not None
            else json.dumps(obj).encode()
            if obj is not None else None
        )

        headers = (
            {'Authorization': 'Bearer ' + token}
            if token else {}
        )

        request = urllib.request.Request(
            f'http://127.0.0.1:{port}' + path,
            data=data,
            headers=headers
        )

        with urllib.request.urlopen(
            request,
            timeout=5
        ) as res:
            return json.loads(res.read())

    def denied(status, *args, **kw):
        try:
            req(*args, **kw)
        except urllib.error.HTTPError as e:
            assert e.code == status, (e.code, status)
            return

        raise AssertionError(
            'Request unexpectedly accepted'
        )

    proc = start_server()

    try:
        for _ in range(150):
            if proc.poll() is not None:
                raise RuntimeError(
                    f'Initial server exited during startup '
                    f'(exit={proc.returncode})'
                )

            try:
                socket.create_connection(
                    ('127.0.0.1', port), .2
                ).close()
                break
            except OSError:
                time.sleep(.05)
        else:
            raise RuntimeError(
                'Initial server did not become ready within 7.5 seconds'
            )

        a, b, c = [
            req(
                '/register',
                {'name': name, 'password': 'password123'}
            )['token']
            for name in ('Alice', 'Bob', 'Carol')
        ]

        users = req('/state', token=a)['users']
        bob = next(
            x['id'] for x in users
            if x['name'] == 'Bob'
        )

        req('/group', {'name': 'Test'}, a)
        gid = req('/state', token=a)['groups'][0]['id']

        denied(
            403, '/message',
            {'gid': gid, 'body': 'intrusion'}, b
        )
        denied(
            403, '/invite',
            {'gid': gid, 'uid': bob}, c
        )

        req('/invite', {'gid': gid, 'uid': bob}, a)
        req('/message', {'gid': gid, 'body': 'group hello'}, b)

        assert (
            req('/state', token=a)['messages'][-1]['body']
            == 'group hello'
        )
        assert not req('/state', token=c)['messages']

        denied(
            410,
            '/file_DISABLED?target=' + str(bob),
            token=a,
            raw=b'x'
        )

        denied(
            400, '/upload/start',
            {
                'target': bob,
                'name': 'too-big',
                'size': 5 * 1024**3 + 1
            },
            a
        )

        first = req(
            '/upload/start',
            {
                'target': bob,
                'name': 'boundary',
                'size': 5 * 1024**3
            },
            a
        )['upload_id']

        denied(
            507, '/upload/start',
            {
                'target': bob,
                'name': 'reserved',
                'size': 3 * 1024**3
            },
            a
        )

        denied(
            404, '/upload/cancel',
            {'upload_id': first}, b
        )

        req('/upload/cancel', {'upload_id': first}, a)

        assert req('/storage', token=a)['used_bytes'] == 0

        ident = req(
            '/upload/start',
            {
                'target': bob,
                'name': 'small',
                'size': 3
            },
            a
        )['upload_id']

        denied(
            409,
            '/upload/chunk?id=' + ident + '&offset=1',
            token=a,
            raw=b'abc'
        )

        req(
            '/upload/chunk?id=' + ident + '&offset=0',
            token=a,
            raw=b'abc'
        )

        denied(
            409, '/upload/finish',
            {'upload_id': ident, 'sha256': 'bad'}, a
        )

        req('/upload/finish', {'upload_id': ident}, a)

        fid = req('/state', token=b)['files'][0]['id']

        denied(
            403,
            '/download?id=' + str(fid),
            token=c
        )

        with sqlite3.connect(
            root / 'server_data/lan.db'
        ) as db:
            db.execute('UPDATE files SET expires=0')

        assert req('/storage', token=a)['used_bytes'] == 3

        stop_server(proc)
        proc = None

        proc = start_server()

        for _ in range(150):
            if proc.poll() is not None:
                raise RuntimeError(
                    f'Restarted server exited '
                    f'(exit={proc.returncode})'
                )

            try:
                if req('/storage', token=a)['used_bytes'] == 0:
                    break
            except (OSError, urllib.error.URLError):
                pass

            time.sleep(.05)
        else:
            raise RuntimeError(
                'Restarted server failed readiness/expiry '
                'check within 7.5 seconds'
            )

        assert (
            req('/state', token=b)['files'][0]['expired']
            == 1
        )

        assert not list(
            (root / 'server_data/files').iterdir()
        )

        print(
            'PASS: group isolation, legacy bypass blocked, '
            '5 GiB boundary (reservation only), disk reservations, '
            'owner-only cancellation, offsets, checksum rejection, '
            'download ACL, physical expiry and metadata retention'
        )

    finally:
        if proc is not None:
            stop_server(proc)

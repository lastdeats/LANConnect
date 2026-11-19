
import os
import sys
import tempfile
import subprocess
import time
import json
import urllib.request
import urllib.error
import socket
from pathlib import Path

src = Path(__file__).resolve().parent

with tempfile.TemporaryDirectory() as td:
    (Path(td) / 'live.py').write_bytes(
        (src / 'live.py').read_bytes()
    )

    server = Path(td) / 'server.py'
    server.write_text(
        (src / 'server.py').read_text(encoding='utf-8').replace(
            "if __name__=='__main__':",
            "shutil.disk_usage=lambda path: type('Disk',(),{'free':100*1024**3})()\nif __name__=='__main__':"
        ),
        encoding='utf-8'
    )

    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()

    env = dict(
        os.environ,
        LAN_HOST='127.0.0.1',
        LAN_PORT=str(port),
        LAN_STORAGE_GIB='5',
        LAN_MIN_FREE_GIB='1'
    )

    p = subprocess.Popen(
        [sys.executable, str(server)],
        cwd=td,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE
    )

    def req(path, obj=None, token=None):
        data = json.dumps(obj).encode() if obj is not None else None
        headers = {'Authorization': 'Bearer ' + token} if token else {}

        with urllib.request.urlopen(
            urllib.request.Request(
                f'http://127.0.0.1:{port}{path}',
                data=data,
                headers=headers
            ),
            timeout=10
        ) as r:
            return json.loads(r.read())

    try:
        for _ in range(60):
            try:
                socket.create_connection(
                    ('127.0.0.1', port), .1
                ).close()
                break
            except OSError:
                time.sleep(.1)

        token = req(
            '/register',
            {'name': 'Alice', 'password': 'password123'}
        )['token']

        token2 = req(
            '/register',
            {'name': 'Bob', 'password': 'password123'}
        )['token']

        bob = next(
            u['id']
            for u in req('/state', token=token)['users']
            if u['name'] == 'Bob'
        )

        info = req('/storage', token=token)

        assert info['limit_bytes'] == 5 * 1024**3
        assert info['used_bytes'] == 0

        # Reserve nearly 5 GiB without writing a huge file.
        req(
            '/upload/start',
            {
                'target': bob,
                'size': 5 * 1024**3 - 1024,
                'name': 'large.bin'
            },
            token
        )

        assert (
            req('/storage', token=token)['used_bytes']
            == 5 * 1024**3 - 1024
        )

        try:
            req(
                '/upload/start',
                {
                    'target': bob,
                    'size': 2048,
                    'name': 'other.bin'
                },
                token
            )
            raise AssertionError('Quota was not enforced')

        except urllib.error.HTTPError as e:
            assert e.code == 507, e.code

        print(
            'PASS: quota reservation, storage status '
            'and rejection on overflow'
        )

    finally:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()

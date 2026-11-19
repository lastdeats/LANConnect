"""No proxy routing or forwarding of credentials to redirects."""
import threading, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from network import urlopen
hits=[]
class Destination(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        hits.append(self.headers.get('Authorization'));self.send_response(200);self.end_headers()
class Source(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path=='/redirect':
            self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{destination.server_port}/');self.end_headers()
        else:
            self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'OK')
destination=ThreadingHTTPServer(('127.0.0.1',0),Destination)
source=ThreadingHTTPServer(('127.0.0.1',0),Source)
for server in (destination,source):
    server.timeout = 0.25
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
try:
    with urlopen(f'http://127.0.0.1:{source.server_port}/') as response:assert response.read()==b'OK'
    try:
        urlopen(urllib.request.Request(f'http://127.0.0.1:{source.server_port}/redirect',headers={'Authorization':'Bearer test-only'}))
        raise AssertionError('redirect followed')
    except urllib.error.HTTPError as error:assert error.code==302
    assert hits==[]
    print('PASS: direct transport, redirect rejected, bearer not forwarded')
finally:
    for server in (source,destination):
        server.shutdown()
        server.server_close()

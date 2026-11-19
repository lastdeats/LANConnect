"""Shared client transport: honour configured TLS trust and reject redirects.
No ambient proxy: all requests go directly to the selected LAN server.
"""
import os
import ssl
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_opener():
    context = ssl.create_default_context(cafile=os.getenv('LAN_CA_CERT') or None)
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                       urllib.request.HTTPSHandler(context=context))


OPENER = make_opener()


def urlopen(request, timeout=30):
    return OPENER.open(request, timeout=timeout)

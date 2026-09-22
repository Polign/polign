"""HTTPS checks for the test service, including locally unresolvable Quick Tunnel names."""

import http.client
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a webhook bearer token to a redirect destination.
        return None


@lru_cache(maxsize=16)
def public_address(host):
    query = urllib.parse.urlencode({"name": host, "type": "A"})
    with urllib.request.urlopen(
        "https://dns.google/resolve?" + query, timeout=5
    ) as response:
        result = json.load(response)
    for answer in result.get("Answer", []) if result.get("Status") == 0 else []:
        if answer.get("type") == 1:
            address = ipaddress.ip_address(answer["data"])
            if address.version == 4 and address.is_global:
                return str(address)
    raise OSError("Public DNS did not return an address for the temporary tunnel")


def open_url(request, *, timeout):
    opener = urllib.request.build_opener(NoRedirect())
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.URLError as exc:
        url = (
            request.full_url if isinstance(request, urllib.request.Request) else request
        )
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        if not (
            isinstance(exc.reason, socket.gaierror)
            and parsed.scheme == "https"
            and re.fullmatch(r"[a-z0-9-]+\.trycloudflare\.com", host)
        ):
            raise
    address = public_address(host)

    def connect(target, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        destination = (address, target[1]) if target[0] == host else target
        return socket.create_connection(destination, timeout, source_address)

    def connection(hostname, **kwargs):
        client = http.client.HTTPSConnection(hostname, **kwargs)
        # Change only DNS resolution. HTTPSConnection still verifies the original
        # hostname and uses it for SNI and Host. Certificate checks stay enabled.
        client._create_connection = connect
        return client

    class ResolvedHTTPS(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(connection, req)

    return urllib.request.build_opener(NoRedirect(), ResolvedHTTPS()).open(
        request, timeout=timeout
    )

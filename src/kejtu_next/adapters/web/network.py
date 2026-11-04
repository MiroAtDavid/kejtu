"""Public-only browser egress. DNS is checked and the validated address is used.

Chromium uses this temporary authenticated loopback proxy for HTTP(S), including
redirects and subresources. No ambient proxy settings or second DNS resolution.
"""
from contextlib import contextmanager
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import secrets
import select
import socket
import threading
import time
from urllib.parse import urlsplit


def public_url(url):
    if not isinstance(url, str) or not 1 <= len(url) <= 2048 or any(ord(c) < 33 for c in url) or '\\' in url:
        raise ValueError('Use a public HTTP(S) URL without spaces or credentials.')
    try:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii').lower().rstrip('.')
        if port not in (80, 443) or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.onion')) or '%' in host:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ValueError('Only public HTTP(S) pages on ports 80/443 are supported.') from None
    return parsed, host, port


def public_address(value):
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast:
        return False
    if address.version == 6:
        if address.ipv4_mapped:
            return public_address(str(address.ipv4_mapped))
        if address.sixtofour or address.teredo or address in ipaddress.ip_network('64:ff9b::/96') or address in ipaddress.ip_network('64:ff9b:1::/48'):
            return False
    return True


def connect_public(host, port, timeout=5):
    if port not in (80, 443):
        raise ValueError('Only public HTTP(S) ports are allowed.')
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not public_address(row[4][0]) for row in addresses):
        raise ValueError('Local, private or otherwise non-public network destinations are not allowed.')
    error = None
    for family, kind, proto, _, address in addresses:
        connection = socket.socket(family, kind, proto)
        try:
            connection.settimeout(timeout)
            connection.connect(address)  # The exact address checked above, never host again.
            return connection
        except OSError as exc:
            error = exc
            connection.close()
    raise OSError('The public website could not be reached.') from error


class Proxy(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, timeout):
        self.password = secrets.token_urlsafe(32)
        self.auth = 'Basic ' + base64.b64encode(('kejtu:' + self.password).encode()).decode()
        self.deadline = time.monotonic() + timeout
        self.guard = threading.Lock()
        self.transferred = 0
        self.connections = 0
        self.blocked = 0
        super().__init__(('127.0.0.1', 0), Handler)

    def consume(self, count=0, connection=False):
        with self.guard:
            self.transferred += count
            self.connections += int(connection)
            return time.monotonic() < self.deadline and self.transferred <= 40_000_000 and self.connections <= 120


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def denied(self, code=403):
        self.close_connection = True
        self.send_response(code)
        if code == 407:
            self.send_header('Proxy-Authenticate', 'Basic realm="Kejtu reader"')
        self.send_header('Content-Length', '0')
        self.send_header('Connection', 'close')
        self.end_headers()

    def authorized(self):
        if not secrets.compare_digest(self.headers.get('Proxy-Authorization', ''), self.server.auth):
            self.denied(407)
            return False
        if not self.server.consume(connection=True):
            self.denied(429)
            return False
        return True

    def relay(self, upstream, *, duplex):
        upstream.settimeout(2)
        peers = [upstream, self.connection] if duplex else [upstream]
        while self.server.consume():
            readable, _, _ = select.select(peers, [], [], .2)
            for source in readable:
                data = source.recv(65536)
                if not data or not self.server.consume(len(data)):
                    return
                destination = upstream if source is self.connection else self.connection
                destination.sendall(data)

    def do_CONNECT(self):
        if not self.authorized():
            return
        try:
            parsed, host, port = public_url('https://' + self.path)
            if parsed.path or parsed.query or parsed.fragment or port != 443:
                raise ValueError('Invalid HTTPS tunnel destination.')
            upstream = connect_public(host, port)
        except (ValueError, OSError):
            self.server.blocked += 1
            self.denied()
            return
        self.close_connection = True
        try:
            with upstream:
                self.send_response(200, 'Connection established')
                self.end_headers()
                self.wfile.flush()
                self.relay(upstream, duplex=True)
        except OSError:
            pass

    def do_GET(self):
        if not self.authorized():
            return
        try:
            parsed, host, port = public_url(self.path)
            if parsed.scheme != 'http' or port != 80 or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0':
                raise ValueError('Only plain HTTP reads are supported.')
            upstream = connect_public(host, port)
        except (ValueError, OSError):
            self.server.blocked += 1
            self.denied()
            return
        self.close_connection = True
        try:
            with upstream:
                path = parsed.path or '/'
                if parsed.query:
                    path += '?' + parsed.query
                headers = [(k, v) for k, v in self.headers.items() if k.lower() not in
                           ('host', 'proxy-authorization', 'proxy-connection', 'connection', 'upgrade', 'keep-alive')]
                authority = '[' + host + ']' if ':' in host else host
                message = f'{self.command} {path} HTTP/1.1\r\nHost: {authority}\r\nConnection: close\r\n'
                message += ''.join(f'{k}: {v}\r\n' for k, v in headers) + '\r\n'
                upstream.sendall(message.encode('latin-1'))
                self.relay(upstream, duplex=False)
        except (OSError, UnicodeError):
            pass

    do_HEAD = do_GET


@contextmanager
def public_proxy(timeout=20):
    server = Proxy(timeout)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.deadline = 0
        server.shutdown()
        server.server_close()
        thread.join(1)

"""Versioned local service authentication and native Jellyfin context adapter."""
import hmac
import ipaddress
from pathlib import Path
import re
from urllib.parse import urlparse

import requests


def local_url(value, allow_path=False):
    parsed = urlparse(value)
    try:
        loopback = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname or '').is_loopback
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        loopback = valid_port = False
    if not loopback or not valid_port or parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.query or parsed.fragment or (not allow_path and parsed.path not in ('', '/')):
        raise ValueError('Plugin connections require a loopback HTTP(S) URL without credentials, query, or fragment')
    return value.rstrip('/')


def read_secret(settings):
    path = Path(settings.get('bridge_token_file', ''))
    if not path.is_absolute():
        raise RuntimeError('Configure the absolute plugin pairing-file path')
    try:
        value = path.read_text(encoding='ascii').strip()
    except (OSError, UnicodeError):
        raise RuntimeError('Cannot read the private plugin pairing file') from None
    if not re.fullmatch(r'[0-9A-Fa-f]{64}', value):
        raise RuntimeError('The plugin pairing file is invalid')
    return value


def authorized(settings, authorization, client_host):
    try:
        if not ipaddress.ip_address(client_host or '').is_loopback:
            return False
        expected = read_secret(settings)
    except (ValueError, RuntimeError):
        return False
    supplied = authorization[7:] if authorization.startswith('Bearer ') else ''
    return bool(re.fullmatch(r'[0-9A-Fa-f]{64}', supplied)) and hmac.compare_digest(expected, supplied)


class NativeJellyfin:
    def __init__(self, config):
        self.config = config

    def request(self, endpoint, method='GET'):
        settings = self.config.get()
        base = local_url(settings['jellyfin_url'], allow_path=True)
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.request(method, base + '/Remuxify/Bridge/' + endpoint,
                                           headers={'X-Remuxify-Key': read_secret(settings)},
                                           timeout=(3, 12), allow_redirects=False)
        except requests.RequestException:
            raise RuntimeError('Cannot reach the local Jellyfin plugin; check its URL and status') from None
        if response.status_code not in (200, 202):
            raise RuntimeError('The native Jellyfin plugin connection is unavailable or unauthorized')
        return response

    def context(self):
        try:
            value = self.request('Context').json()
            if value.get('protocolVersion') != 1 or not isinstance(value.get('roots'), list) or not isinstance(value.get('playbackPaths'), list):
                raise ValueError()
            if any(not isinstance(root, dict) or not isinstance(root.get('library'), str) or not isinstance(root.get('path'), str) or not root['path'] for root in value['roots']):
                raise ValueError()
            if any(not isinstance(path, str) or not path for path in value['playbackPaths']):
                raise ValueError()
            return value
        except (ValueError, AttributeError):
            raise RuntimeError('Unexpected native Jellyfin context; refusing unsafe fallback') from None

    def refresh(self):
        self.request('Refresh', method='POST')

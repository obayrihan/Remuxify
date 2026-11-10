"""Local qBittorrent coordination. No torrent deletion, rechecking, or auto-resume."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

import requests

from core import DATA, norm


class TorrentHold(Exception):
    pass


def crypt(data, decrypt=False):
    """Machine DPAPI allows the SYSTEM worker to read a connection saved locally.

    The encrypted file also has an explicit owner/SYSTEM/Administrators DACL.
    """
    if os.name != 'nt':
        raise RuntimeError('Credential storage requires Windows DPAPI')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if decrypt:
        fn = api.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        fn = api.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), 'Media Forge local connection', None, None, None, 5, ctypes.byref(output))
    if not ok:
        raise RuntimeError('Windows could not unlock/save the local connection')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class Credentials:
    def __init__(self, path=DATA / 'qbittorrent-credentials.dpapi'):
        self.path = Path(path)
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            if not self.path.exists():
                return {'enabled': False, 'port': 8080}
            try:
                return json.loads(crypt(self.path.read_bytes(), decrypt=True))
            except Exception:
                raise TorrentHold('Cannot unlock qBittorrent connection; reconnect in Settings') from None

    def status(self):
        value = self.read()
        return {'enabled': value['enabled'], 'port': value['port'],
                'configured': bool(value.get('username') and value.get('password'))}

    def save(self, value):
        from permissions import owner_sid, set_dacl_only
        encrypted = crypt(json.dumps(value).encode('utf-8'))
        pending = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        with self.lock:
            try:
                with pending.open('xb') as stream:
                    owner = owner_sid(self.path.parent)
                    set_dacl_only(pending, 'D:P' + ''.join(f'(A;;FA;;;{sid})' for sid in (owner, 'SY', 'BA')))
                    stream.write(encrypted)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(pending, self.path)
            finally:
                if pending.exists():
                    pending.unlink()


class Client:
    def __init__(self, settings):
        self.base = 'http://127.0.0.1:' + str(settings['port'])
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers['Referer'] = self.base
        try:
            response = self.request('POST', 'auth/login', data={
                'username': settings.get('username', ''), 'password': settings.get('password', '')})
            if response.text.strip() != 'Ok.' or not self.session.cookies.get('SID'):
                raise TorrentHold('qBittorrent login rejected; reconnect in Forge Settings')
            version = self.request('GET', 'app/version').text.strip()
            match = re.fullmatch(r'v?(\d+)\.\d+[^\s]*', version)
            if not match or int(match[1]) not in (4, 5):
                raise TorrentHold('Unsupported qBittorrent version; original retained')
            self.stop_method = 'stop' if int(match[1]) >= 5 else 'pause'
        except Exception:
            self.close()
            raise

    def request(self, method, endpoint, **kwargs):
        try:
            response = self.session.request(method, self.base + '/api/v2/' + endpoint,
                                            timeout=(3, 10), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise TorrentHold('Local qBittorrent API unavailable; original retained') from None
        if response.status_code != 200:
            raise TorrentHold('qBittorrent API refused the request; check the local connection')
        return response

    def json(self, endpoint, **params):
        try:
            result = self.request('GET', endpoint, params=params).json()
            if not isinstance(result, list):
                raise ValueError()
            return result
        except ValueError:
            raise TorrentHold('Unexpected qBittorrent response; original retained') from None

    def matching(self, path):
        target = norm(path)
        matches = []
        for torrent in self.json('torrents/info'):
            base = Path(torrent['save_path'])
            try:
                Path(target).relative_to(Path(norm(base)))
            except ValueError:
                continue
            torrent_hash = torrent.get('hash', '')
            if not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', torrent_hash):
                raise TorrentHold('Invalid torrent identifier; original retained')
            files = self.json('torrents/files', hash=torrent_hash)
            selected = [f for f in files if norm(base / f['name']) == target]
            if not selected:
                continue
            if float(torrent.get('progress', 0)) < 1 or any(float(f.get('progress', 0)) < 1 for f in selected):
                raise TorrentHold('Matching torrent is still downloading; original retained')
            matches.append(torrent_hash)
        return list(dict.fromkeys(matches))

    def stop_and_wait(self, hashes, interrupt, timeout=30):
        if not hashes:
            return
        self.request('POST', 'torrents/' + self.stop_method, data={'hashes': '|'.join(hashes)})
        deadline = time.monotonic() + timeout
        while True:
            interrupt()
            rows = self.json('torrents/info', hashes='|'.join(hashes))
            states = {r['hash']: r['state'] for r in rows}
            # A removed/missing torrent is ambiguous; never assume it released a lock.
            if all(states.get(h) in ('pausedUP', 'pausedDL', 'stoppedUP', 'stoppedDL') for h in hashes):
                return
            if time.monotonic() >= deadline:
                raise TorrentHold('Waiting for qBittorrent to stop the matching torrent; original retained')
            time.sleep(0.5)

    def close(self):
        self.session.close()


class TorrentCoordinator:
    def __init__(self, credentials=None):
        self.credentials = credentials or Credentials()
        self.lock = threading.RLock()
        self.retry_after = 0
        self.failed_settings = None

    def configure(self, changes):
        if set(changes) - {'enabled', 'port', 'username', 'password'}:
            raise ValueError('Unknown qBittorrent settings')
        value = {**self.credentials.read(), **{k: v for k, v in changes.items() if k != 'password' or v}}
        if type(value.get('enabled')) is not bool or type(value.get('port')) is not int or not 1 <= value['port'] <= 65535:
            raise ValueError('Choose a local port and enable or disable the connection')
        if any(not isinstance(value.get(k, ''), str) or len(value.get(k, '')) > 1024 for k in ('username', 'password')):
            raise ValueError('Invalid qBittorrent credentials')
        if value['enabled']:
            if not value.get('username') or not value.get('password'):
                raise ValueError('Enter the qBittorrent Web UI username and password')
            client = Client(value)  # Test login before persisting or enabling anything.
            client.close()
        with self.lock:
            self.credentials.save(value)
            self.retry_after = 0
            self.failed_settings = None
        return self.credentials.status()

    def guard(self, path, interrupt, event):
        value = self.credentials.read()
        if not value['enabled']:
            return
        with self.lock:
            if value == self.failed_settings and time.monotonic() < self.retry_after:
                raise TorrentHold('qBittorrent connection needs attention; reconnect in Settings')
            try:
                client = Client(value)
            except TorrentHold:
                self.failed_settings = value
                self.retry_after = time.monotonic() + 300
                raise
        try:
            hashes = client.matching(path)
            client.stop_and_wait(hashes, interrupt)
            if hashes:
                event(f'Stopped {len(hashes)} matching qBittorrent torrent(s); they will remain stopped because replacement changes file hashes')
        finally:
            client.close()

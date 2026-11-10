import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qbittorrent import Client, Credentials, TorrentCoordinator, TorrentHold


class Response:
    status_code = 200
    def __init__(self, text='', data=None):
        self.text, self.data = text, data
    def json(self):
        return self.data


class Session:
    def __init__(self):
        self.headers = {}
        self.cookies = {'SID': 'disposable-test-cookie'}
        self.calls = []
        self.torrents = []
        self.files = {}
        self.closed = False
        self.version = 'v5.1.2'
    def request(self, method, url, **kwargs):
        endpoint = url.split('/api/v2/')[1]
        self.calls.append((method, endpoint, kwargs))
        if endpoint == 'auth/login': return Response('Ok.')
        if endpoint == 'app/version': return Response(self.version)
        if endpoint == 'torrents/info': return Response(data=self.torrents)
        if endpoint == 'torrents/files': return Response(data=self.files[kwargs['params']['hash']])
        if endpoint in ('torrents/stop', 'torrents/pause'):
            hashes = kwargs['data']['hashes'].split('|')
            for torrent in self.torrents:
                if torrent['hash'] in hashes: torrent['state'] = 'stoppedUP' if self.version.startswith('v5') else 'pausedUP'
            return Response()
        raise AssertionError(endpoint)
    def close(self): self.closed = True


class MemoryCredentials:
    def __init__(self, value): self.value = value
    def read(self): return dict(self.value)
    def save(self, value): self.value = dict(value)
    def status(self):
        return {'enabled': self.value['enabled'], 'port': self.value['port'],
                'configured': bool(self.value.get('password'))}


class TorrentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='forge-torrent-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'movie' / 'film.mp4'
        self.settings = {'enabled': True, 'port': 8080, 'username': 'test', 'password': 'test-secret'}
        self.session = Session()
        self.patch = patch('qbittorrent.requests.Session', return_value=self.session)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.hash = 'a' * 40
        self.session.torrents = [dict(hash=self.hash, save_path=str(self.root), progress=1, state='uploading'),
                                 dict(hash='b' * 40, save_path=str(self.root), progress=1, state='uploading')]
        self.session.files = {self.hash: [dict(name='movie/film.mp4', progress=1)],
                              'b' * 40: [dict(name='other/film.mp4', progress=1)]}

    def test_exact_path_only_and_no_resume_delete_or_recheck(self):
        messages = []
        TorrentCoordinator(MemoryCredentials(self.settings)).guard(self.target, lambda: None, messages.append)
        mutations = [(name, args['data']) for method, name, args in self.session.calls
                     if method == 'POST' and name != 'auth/login']
        self.assertEqual(mutations, [('torrents/stop', {'hashes': self.hash})])
        self.assertEqual(self.session.torrents[1]['state'], 'uploading')
        self.assertEqual(len(messages), 1)
        self.assertFalse(self.session.trust_env)
        self.assertTrue(self.session.closed)
        self.assertTrue(all(args['allow_redirects'] is False for _, _, args in self.session.calls))

    def test_incomplete_torrent_blocks_without_stopping_anything(self):
        self.session.torrents[0]['progress'] = .99
        with self.assertRaisesRegex(TorrentHold, 'still downloading'):
            TorrentCoordinator(MemoryCredentials(self.settings)).guard(self.target, lambda: None, lambda _: None)
        self.assertFalse(any(name == 'torrents/stop' for _, name, _ in self.session.calls))

    def test_same_filename_elsewhere_does_not_stop_anything(self):
        client = Client(self.settings)
        hashes = client.matching(self.root / 'different' / 'film.mp4')
        self.assertEqual(hashes, [])
        client.stop_and_wait(hashes, lambda: None)
        self.assertFalse(any(name == 'torrents/stop' for _, name, _ in self.session.calls))

    def test_v4_uses_pause_and_waits_for_confirmation(self):
        self.session.version = 'v4.6.7'
        client = Client(self.settings)
        client.stop_and_wait([self.hash], lambda: None)
        self.assertTrue(any(name == 'torrents/pause' for _, name, _ in self.session.calls))

    def test_missing_torrent_after_stop_fails_closed(self):
        client = Client(self.settings)
        self.session.torrents = []
        with self.assertRaisesRegex(TorrentHold, 'Waiting'):
            client.stop_and_wait([self.hash], lambda: None, timeout=0)

    def test_login_failure_is_backed_off_and_never_logged(self):
        connection = TorrentCoordinator(MemoryCredentials(self.settings))
        with patch('qbittorrent.Client', side_effect=TorrentHold('Login rejected')) as factory:
            for _ in range(2):
                with self.assertRaises(TorrentHold):
                    connection.guard(self.target, lambda: None, lambda _: None)
            self.assertEqual(factory.call_count, 1)

    def test_disabled_connection_never_contacts_api(self):
        connection = TorrentCoordinator(MemoryCredentials({**self.settings, 'enabled': False}))
        connection.guard(self.target, lambda: None, lambda _: None)
        self.assertEqual(self.session.calls, [])

    def test_failed_configuration_does_not_persist_or_stop_torrents(self):
        creds = MemoryCredentials({'enabled': False, 'port': 8080})
        connection = TorrentCoordinator(creds)
        with patch('qbittorrent.Client', side_effect=TorrentHold('Login rejected')):
            with self.assertRaises(TorrentHold): connection.configure(self.settings)
        self.assertFalse(creds.value['enabled'])
        self.assertNotIn('password', creds.value)

    def test_blank_password_keeps_existing_and_status_is_redacted(self):
        creds = MemoryCredentials(self.settings)
        connection = TorrentCoordinator(creds)
        status = connection.configure({'enabled': True, 'port': 8080, 'password': ''})
        self.assertEqual(creds.value['password'], 'test-secret')
        self.assertEqual(set(status), {'enabled', 'configured', 'port'})
        self.assertFalse(any(name == 'torrents/stop' for _, name, _ in self.session.calls))

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_real_dpapi_roundtrip_has_no_plaintext_and_has_private_acl(self):
        import ctypes
        from ctypes import wintypes
        from permissions import _api
        file = self.root / 'credentials.dpapi'
        creds = Credentials(file)
        creds.save(self.settings)
        self.assertEqual(creds.read(), self.settings)
        self.assertNotIn(b'test-secret', file.read_bytes())
        self.assertNotIn(b'username', file.read_bytes())
        adv, kernel = _api()
        descriptor, text = ctypes.c_void_p(), wintypes.LPWSTR()
        result = adv.GetNamedSecurityInfoW(str(file), 1, 4, None, None, None, None, ctypes.byref(descriptor))
        self.assertEqual(result, 0)
        adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                                           wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
        try:
            self.assertTrue(adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, ctypes.byref(text), None))
            acl = text.value
            self.assertIn('D:P', acl)
            self.assertNotIn(';;;BU)', acl)
            self.assertNotIn(';;;AU)', acl)
            self.assertIn(';;;SY)', acl)
            self.assertIn(';;;BA)', acl)
        finally:
            if text: kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
            kernel.LocalFree(descriptor)


if __name__ == '__main__':
    unittest.main()

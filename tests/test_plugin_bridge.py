import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

from core import Config, Jellyfin, norm
from plugin_bridge import NativeJellyfin, authorized, local_url
from pair_worker import pair


class PluginBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='remuxify-bridge-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.secret = self.root / 'test-pairing.secret'
        self.secret.write_text('A' * 64)
        self.settings = {**Config().get(), 'integration_mode':'plugin', 'bridge_token_file':str(self.secret),
                         'jellyfin_url':'http://127.0.0.1:8097/base', 'host':'127.0.0.1'}
        self.config = SimpleNamespace(get=lambda: dict(self.settings))

    def test_service_auth_requires_exact_secret_and_loopback(self):
        header = 'Bearer ' + 'A'*64
        self.assertTrue(authorized(self.settings, header, '127.0.0.1'))
        self.assertTrue(authorized(self.settings, header, '::1'))
        for token, host in [(header,'203.0.113.5'), ('Bearer '+'B'*64,'127.0.0.1'), ('','127.0.0.1')]:
            self.assertFalse(authorized(self.settings, token, host))

    def test_invalid_pairing_and_nonlocal_endpoints_fail_closed(self):
        for value in ('http://example.com', 'http://user:password@localhost', 'file:///private', 'http://127.0.0.1/?key=1'):
            with self.assertRaises(ValueError): local_url(value)
        self.secret.write_text('broken')
        self.assertFalse(authorized(self.settings, 'Bearer '+'A'*64, '127.0.0.1'))

    def test_native_context_request_is_private_versioned_and_has_no_proxy_or_redirects(self):
        context={'protocolVersion':1, 'roots':[{'library':'Test','path':str(self.root)}], 'playbackPaths':[]}
        session=MagicMock(); session.__enter__.return_value=session
        session.request.return_value.status_code=200
        session.request.return_value.json.return_value=context
        with patch('plugin_bridge.requests.Session',return_value=session):
            self.assertEqual(NativeJellyfin(self.config).context(),context)
        self.assertFalse(session.trust_env)
        args, kwargs=session.request.call_args
        self.assertEqual(args[1],'http://127.0.0.1:8097/base/Remuxify/Bridge/Context')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['headers']['X-Remuxify-Key'],'A'*64)

    def test_plugin_mode_uses_native_roots_playback_and_refresh_without_database_or_xml(self):
        context={'protocolVersion':1,'roots':[{'library':'Test','path':str(self.root)}],'playbackPaths':[str(self.root/'file.mp4')]}
        jellyfin=Jellyfin(self.config)
        with patch('plugin_bridge.NativeJellyfin.context',return_value=context), patch('core.sqlite3.connect') as database, patch('core.ET.parse') as xml:
            self.assertEqual(jellyfin.roots(),context['roots'])
            self.assertEqual(jellyfin.playback_paths(),[norm(self.root/'file.mp4')])
            with self.assertRaises(RuntimeError): jellyfin.key()
            database.assert_not_called();xml.assert_not_called()
        with patch('plugin_bridge.NativeJellyfin.refresh') as refresh:
            jellyfin.refresh();refresh.assert_called_once()

    def test_unknown_protocol_or_missing_paths_are_rejected(self):
        for value in ({'protocolVersion':2,'roots':[],'playbackPaths':[]}, {'protocolVersion':1,'roots':[{}],'playbackPaths':[]}, {'protocolVersion':1,'roots':[],'playbackPaths':[None]}):
            response=SimpleNamespace(json=lambda:value)
            with patch.object(NativeJellyfin,'request',return_value=response):
                with self.assertRaises(RuntimeError): NativeJellyfin(self.config).context()

    def test_plugin_config_requires_a_private_listener_and_readable_pairing(self):
        path=self.root/'config.json'
        for host in ('127.0.0.1','::1'):
            path.write_text(json.dumps({**self.settings,'host':host}));Config(path)
        path.write_text(json.dumps({**self.settings,'host':'0.0.0.0'}))
        with self.assertRaises(ValueError):Config(path)

    def test_pairing_failure_keeps_existing_configuration_and_success_never_copies_secret(self):
        path=self.root/'config.json'; original=Config().get(); path.write_text(json.dumps(original))
        with patch('plugin_bridge.NativeJellyfin.context',side_effect=RuntimeError('Not paired')):
            with self.assertRaises(RuntimeError):pair(path,self.secret,'http://127.0.0.1:8097')
        self.assertEqual(json.loads(path.read_text()),original)
        with patch('plugin_bridge.NativeJellyfin.context',return_value={'roots':[]}):
            result=pair(path,self.secret,'http://127.0.0.1:8097')
        self.assertTrue(result['paired'])
        self.assertNotIn('A'*64,path.read_text())
        self.assertEqual(json.loads(path.read_text())['integration_mode'],'plugin')


class PluginWorkerApiTests(unittest.TestCase):
    def setUp(self):
        import app
        from fastapi.testclient import TestClient
        self.app=app
        self.temp=tempfile.TemporaryDirectory(prefix='remuxify-worker-auth-');self.addCleanup(self.temp.cleanup)
        secret=Path(self.temp.name)/'pairing.secret';secret.write_text('A'*64)
        self.patch=patch.dict(app.config.value, integration_mode='plugin', bridge_token_file=str(secret))
        self.patch.start();self.addCleanup(self.patch.stop)
        self.client=TestClient(app.app,client=('127.0.0.1',12345))

    def test_native_service_auth_replaces_standalone_cookie_and_no_pairing_path_is_returned(self):
        self.client.cookies.set('forge_session','obsolete-test-cookie')
        self.assertEqual(self.client.get('/forge/api/me').status_code,401)
        headers={'Authorization':'Bearer '+'A'*64}
        self.assertEqual(self.client.get('/forge/api/me',headers=headers).status_code,200)
        response=self.client.get('/forge/api/settings',headers=headers)
        self.assertEqual(response.status_code,200)
        self.assertNotIn('bridge_token_file',response.json())
        self.assertNotIn('A'*64,response.text)

    def test_plugin_mode_disables_all_standalone_login_bridges(self):
        for path in ('/forge/api/login','/forge/api/logout','/forge/auth/bridge'):
            response=self.client.post(path,headers={'X-Forge-Request':'1'})
            self.assertEqual(response.status_code,404)

    def test_missing_or_invalid_service_key_cannot_mutate(self):
        for header in ('','Bearer '+'B'*64):
            response=self.client.post('/forge/api/scan',headers={'X-Forge-Request':'1','Authorization':header})
            self.assertEqual(response.status_code,401)

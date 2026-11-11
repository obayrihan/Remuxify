"""Auth and request protection tests. No lifespan, scanners, or production worker is started."""
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import app


class Response:
    ok=True
    def __init__(self, value): self.value=value
    def json(self): return self.value


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app.app)
        app.login_attempts.clear()
        app.sessions.clear()

    def test_private_routes_require_login(self):
        for route in ('state','media','jobs','settings','events','qbittorrent'):
            self.assertEqual(self.client.get('/forge/api/'+route).status_code,401)

    def test_mutation_requires_request_header(self):
        self.assertEqual(self.client.post('/forge/api/scan').status_code,403)

    def test_cross_origin_mutation_is_rejected(self):
        response=self.client.post('/forge/api/login',json={'username':'test','password':'test'},headers={'X-Forge-Request':'1','Origin':'https://untrusted.invalid'})
        self.assertEqual(response.status_code,403)

    def test_public_alias_requires_explicit_configuration(self):
        settings = dict(app.config.get(), public_aliases=['https://admin.example.test'])
        with patch.object(app.config, 'get', return_value=settings):
            self.assertTrue(app.allowed_origin('https://admin.example.test'))
            self.assertFalse(app.allowed_origin('https://other.example.test'))
            self.assertFalse(app.allowed_origin('http://admin.example.test'))
        with patch.object(app.config, 'get', return_value=dict(settings, public_aliases=[])):
            self.assertFalse(app.allowed_origin('https://admin.example.test'))

    def test_public_alias_rejects_wildcards_and_embedded_credentials(self):
        for alias in ('https://*.example.test', 'https://user:password@example.test', 'file:///tmp/example'):
            with self.subTest(alias=alias), patch.object(app.config, 'get', return_value=dict(app.config.get(), public_aliases=[alias])):
                with self.assertRaises(ValueError):
                    app.configured_public_origins()

    def test_nonadmin_session_is_rejected(self):
        with patch.object(app.jellyfin,'request',return_value=Response({'Name':'viewer','Policy':{'IsAdministrator':False}})):
            response=self.client.post('/forge/auth/bridge',content='token=test-only',headers={'Origin':'http://localhost:8096','Content-Type':'application/x-www-form-urlencoded'},follow_redirects=False)
        self.assertEqual(response.status_code,403)
        self.assertFalse(app.sessions)

    def test_admin_bridge_uses_httponly_cookie_without_url_token(self):
        with patch.object(app.jellyfin,'request',return_value=Response({'Name':'administrator','Policy':{'IsAdministrator':True}})):
            response=self.client.post('/forge/auth/bridge',content='token=test-only',headers={'Origin':'http://localhost:8096','Content-Type':'application/x-www-form-urlencoded'},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.assertEqual(response.headers['location'],'/forge/')
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.assertIn('SameSite=strict',response.headers['set-cookie'])
        self.assertEqual(self.client.get('/forge/api/me').json()['name'],'administrator')

    def test_privilege_revocation_denies_existing_session(self):
        with patch.object(app.jellyfin,'request',return_value=Response({'Name':'administrator','Policy':{'IsAdministrator':True}})):
            self.client.post('/forge/auth/bridge',content='token=test-only',headers={'Origin':'http://localhost:8096'})
        next(iter(app.sessions.values()))['checked']=time.time()-60
        with patch.object(app.jellyfin,'request',return_value=Response({'Name':'viewer','Policy':{'IsAdministrator':False}})):
            self.assertEqual(self.client.get('/forge/api/state').status_code,403)

    def test_response_has_security_headers(self):
        response=self.client.get('/forge/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['referrer-policy'],'no-referrer')
        self.assertIn("script-src 'self'",response.headers['content-security-policy'])

    def test_standalone_page_branding_and_legacy_health_contract(self):
        self.assertIn('<title>Remuxify', self.client.get('/forge/').text)
        self.assertEqual(self.client.get('/healthz').json(), {'ok': True, 'service': 'Media Forge'})

    def test_native_plugin_routes_are_not_part_of_the_standalone_app(self):
        for path in ('/Remuxify/Status', '/Remuxify/Api/state', '/Remuxify/Bridge/Context'):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_torrent_settings_require_admin_for_writes(self):
        response = self.client.put('/forge/api/qbittorrent', json={'enabled':False,'port':8080},
                                   headers={'X-Forge-Request':'1'})
        self.assertEqual(response.status_code, 401)

    def test_torrent_settings_return_only_redacted_status(self):
        app.app.dependency_overrides[app.admin] = lambda: {'name':'test-admin'}
        try:
            with patch.object(app.worker.torrents.credentials, 'status',
                              return_value={'enabled':True,'configured':True,'port':8080}):
                response = self.client.get('/forge/api/qbittorrent')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()), {'enabled','configured','port'})
        finally:
            app.app.dependency_overrides.clear()

    def test_torrent_connection_change_rejected_during_job(self):
        app.app.dependency_overrides[app.admin] = lambda: {'name':'test-admin'}
        try:
            with patch.object(app.worker, 'current', 'disposable-test-job'), patch.object(app.worker.torrents, 'configure') as configure:
                response = self.client.put('/forge/api/qbittorrent', json={'enabled':False,'port':8080},
                                           headers={'X-Forge-Request':'1'})
                self.assertEqual(response.status_code, 409)
                configure.assert_not_called()
        finally:
            app.app.dependency_overrides.clear()


if __name__=='__main__':
    unittest.main(verbosity=2)

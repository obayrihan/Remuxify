"""Optional deployment planning uses disposable configured paths, never a host setup."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import integration


class IntegrationConfigTests(unittest.TestCase):
    def test_plan_uses_configured_paths_hostname_and_port_without_applying(self):
        with tempfile.TemporaryDirectory(prefix='remuxify-plan-') as folder:
            base = Path(folder)
            web = base / 'web'
            web.mkdir()
            original_menu = {'menuLinks': [{'name':'Existing link','url':'/existing'}]}
            (web / 'config.json').write_text(json.dumps(original_menu))
            tunnel = base / 'tunnel.yml'
            original_tunnel = ('ingress:\n  - hostname: jellyfin.example.test\n'
                               '    service: http://127.0.0.1:8096\n  - service: http_status:404\n')
            tunnel.write_text(original_tunnel)
            settings = {'jellyfin_web':str(web), 'cloudflare_config':str(tunnel),
                        'public_url':'https://jellyfin.example.test/forge/', 'port':5057}
            with patch.object(integration, 'Config', return_value=SimpleNamespace(get=lambda: settings)), \
                 patch.object(integration, 'BASE', base), patch.object(integration, 'STAGING', base / 'staging'):
                plan = integration.plan()
            staged = (base / 'staging/cloudflared-config.yml').read_text()
            self.assertIn('service: http://127.0.0.1:5057', staged)
            self.assertIn('service: http://127.0.0.1:8096', staged)
            self.assertIn('service: http_status:404', staged)
            self.assertEqual(plan['public_url'], settings['public_url'])
            self.assertEqual(tunnel.read_text(), original_tunnel)
            self.assertEqual(json.loads((web / 'config.json').read_text()), original_menu)
            menu = json.loads((base / 'staging/jellyfin-web-config.json').read_text())['menuLinks']
            self.assertEqual(menu[0], original_menu['menuLinks'][0])

    def test_missing_configured_paths_are_rejected(self):
        with patch.object(integration, 'Config', return_value=SimpleNamespace(get=lambda: {})):
            with self.assertRaisesRegex(RuntimeError, 'jellyfin_web'):
                integration.configured_paths()

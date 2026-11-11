"""A standalone build accepts legacy settings and refuses the paired deployment."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import Config


class StandaloneConfigTests(unittest.TestCase):
    def test_legacy_and_explicit_standalone_configuration_are_accepted(self):
        with tempfile.TemporaryDirectory(prefix='remuxify-standalone-') as folder:
            path = Path(folder) / 'config.json'
            for settings in ({}, {'integration_mode': 'standalone'}):
                path.write_text(json.dumps(settings), encoding='utf-8')
                config = Config(path)
                self.assertEqual(config.get()['video_decoder'], 'auto')

    def test_plugin_mode_is_rejected_without_database_or_pairing_access(self):
        with tempfile.TemporaryDirectory(prefix='remuxify-standalone-') as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps({'integration_mode': 'plugin', 'bridge_token_file': 'unreadable-pairing.secret'}), encoding='utf-8')
            with patch('core.sqlite3.connect') as database, patch('core.requests.get') as network:
                with self.assertRaisesRegex(ValueError, 'migrate plugin-mode settings'):
                    Config(path)
                database.assert_not_called()
                network.assert_not_called()


if __name__ == '__main__':
    unittest.main()

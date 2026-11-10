import json
from pathlib import Path
import tempfile
import unittest

from core import Config


class PlaybackConfigTests(unittest.TestCase):
    def test_force_defaults_off_and_persists_boolean(self):
        with tempfile.TemporaryDirectory(prefix='forge-force-config-') as folder:
            path = Path(folder) / 'config.json'
            value = Config().get()
            value.pop('force_transcode_during_playback', None)
            path.write_text(json.dumps(value), encoding='utf-8')
            config = Config(path)
            self.assertFalse(config.get()['force_transcode_during_playback'])
            config.update({'force_transcode_during_playback': True})
            self.assertTrue(Config(path).get()['force_transcode_during_playback'])
            with self.assertRaises(ValueError):
                config.update({'force_transcode_during_playback': 'true'})

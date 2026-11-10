import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from core import norm
from worker import WaitForResources, Worker


class PlaybackPolicyTests(unittest.TestCase):
    def setUp(self):
        self.source = r'C:\disposable-source.mp4'
        self.jellyfin = Mock()
        # Even if the global start gate is enabled, unrelated playback must not
        # block promotion of a fully validated replacement.
        config = SimpleNamespace(get=lambda: {'pause_during_playback': True, 'force_transcode_during_playback': True})
        self.worker = Worker(config, None, None, None, self.jellyfin, threading.Event())

    def test_unrelated_playback_does_not_block_replacement(self):
        self.jellyfin.playback_paths.return_value = [norm(r'C:\different-movie.mp4')]
        self.worker.playback_guard(self.source)

    def test_exact_source_playback_still_blocks_replacement(self):
        self.jellyfin.playback_paths.return_value = [norm(self.source)]
        with self.assertRaisesRegex(WaitForResources, 'source is playing'):
            self.worker.playback_guard(self.source)

    def test_unknown_playback_state_still_blocks_replacement(self):
        self.jellyfin.playback_paths.side_effect = RuntimeError('Test outage')
        with self.assertRaisesRegex(WaitForResources, 'before replacement'):
            self.worker.playback_guard(self.source)


if __name__ == '__main__':
    unittest.main()

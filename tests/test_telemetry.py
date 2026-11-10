import json
import math
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from telemetry import WindowsDiskActivity, active_disks_from_idle, disk_io_sample
from core import Config, Resources


class TelemetryTests(unittest.TestCase):
    def test_busiest_disk_is_not_hidden_by_total_average(self):
        disks = active_disks_from_idle([('0 C:', 0, 99), ('1 H:', 1, 20), ('_Total', 0, 60)])
        self.assertEqual(disks, {'0 C:': 1, '1 H:': 80})
        self.assertEqual(max(disks.values()), 80)

    def test_invalid_counter_samples_are_not_reported_as_zero(self):
        disks = active_disks_from_idle([('offline', 0x800007D1, 100), ('invalid', 0, math.nan)])
        self.assertEqual(disks, {})

    def test_windows_read_write_time_is_not_used_as_activity_percent(self):
        old = SimpleNamespace(read_bytes=0, write_bytes=0, read_time=0, write_time=0)
        new = SimpleNamespace(read_bytes=10_000_000, write_bytes=5_000_000, read_time=1, write_time=0)
        mbps, busy = disk_io_sample({'disk': old}, {'disk': new}, 1)
        self.assertEqual(mbps, 15)
        self.assertIsNone(busy)

    def test_native_busy_time_remains_supported(self):
        old = SimpleNamespace(read_bytes=0, write_bytes=0, busy_time=100)
        new = SimpleNamespace(read_bytes=10_000_000, write_bytes=0, busy_time=600)
        mbps, busy = disk_io_sample({'disk': old}, {'disk': new}, 1)
        self.assertEqual(mbps, 10)
        self.assertEqual(busy, 50)

    @unittest.skipUnless(os.name == 'nt', 'Windows sampler integration')
    def test_sampler_preserves_low_cpu_and_uses_native_disk_activity(self):
        stop = Mock()
        stop.wait.side_effect = [False, True]
        inventory = SimpleNamespace(roots=[])
        jellyfin = Mock()
        jellyfin.playback_paths.return_value = []
        disk = Mock()
        disk.read.return_value = {'2 H:': 27.8}
        old = {'disk': SimpleNamespace(read_bytes=0, write_bytes=0)}
        new = {'disk': SimpleNamespace(read_bytes=50_000_000, write_bytes=0)}
        with tempfile.TemporaryDirectory(prefix='forge-telemetry-test-') as folder:
            resources = Resources(Config(), inventory, jellyfin, stop)
            resources.receipt_dir = Path(folder)
            with patch('core.WindowsDiskActivity', return_value=disk), \
                 patch('core.psutil.disk_io_counters', side_effect=[old, new]), \
                 patch('core.psutil.cpu_percent', side_effect=[0, 0.2]), \
                 patch('core.psutil.virtual_memory', return_value=SimpleNamespace(available=16*1024**3)), \
                 patch('core.time.monotonic', side_effect=[0, 5]), \
                 patch('core.run', return_value=SimpleNamespace(returncode=0, stdout='Test GPU, 7, 80, 4, 4096, 8192')):
                resources.loop()
            self.assertEqual(resources.value['cpu'], 0.2)
            self.assertEqual(resources.value['disk_busy'], 27.8)
            self.assertEqual(resources.value['disk_mbps'], 10)
            self.assertFalse(resources.value['disk_estimated'])
            receipt = json.loads((Path(folder)/'resource-sample.json').read_text(encoding='utf-8'))
            self.assertEqual(receipt['disk_busy'], 27.8)
            self.assertNotIn('playback', receipt)
            disk.close.assert_called_once()

    @unittest.skipUnless(os.name == 'nt', 'Windows performance counter integration')
    def test_actual_windows_disk_counter_is_readable(self):
        counter = WindowsDiskActivity()
        try:
            time.sleep(0.3)
            values = counter.read()
            self.assertTrue(values)
            self.assertNotIn('_Total', values)
            self.assertTrue(all(0 <= value <= 100 for value in values.values()))
        finally:
            counter.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)

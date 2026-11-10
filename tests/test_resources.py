import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from core import Config, Resources, norm
import threading


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.resources=Resources(Config(),SimpleNamespace(roots=[]),None,threading.Event())
        self.resources.value={'ready':True,'sampled':time.time(),'cpu':1,'ram_free_gb':16,
                              'gpu':{'free_mb':7000,'utilization':0,'encoder':0},'disk_busy':0,
                              'playback':[],'playback_error':None}

    def reasons(self,active=False):
        with patch('core.psutil.disk_usage',return_value=SimpleNamespace(free=100*1024**3)):
            return self.resources.reasons(r'C:\disposable-test.mp4',1024**3,active=active)

    def test_low_storage_blocks_start(self):
        with patch('core.psutil.disk_usage',return_value=SimpleNamespace(free=2*1024**3)):
            self.assertIn('Low storage',self.resources.reasons(r'C:\disposable-test.mp4',1024**3)[0])

    def test_stale_telemetry_blocks_start(self):
        self.resources.value['sampled']=time.time()-100
        self.assertTrue(self.reasons())

    def test_busy_cpu_gpu_disk_block_start(self):
        self.resources.value.update(cpu=99,disk_busy=100)
        self.resources.value['gpu']['utilization']=99
        reasons=' '.join(self.reasons())
        for keyword in ('CPU','GPU','Disk'):self.assertIn(keyword,reasons)

    def test_busy_decoder_blocks_new_work(self):
        self.resources.value['gpu']['decoder'] = 99
        self.assertIn('GPU', ' '.join(self.reasons()))

    def test_current_job_load_does_not_cause_endless_restarts(self):
        self.resources.value.update(cpu=99,disk_busy=100)
        self.resources.value['gpu']['utilization']=99
        self.assertEqual(self.reasons(active=True),[])

    def test_low_memory_still_interrupts_active_work(self):
        self.resources.value['ram_free_gb']=0.1
        self.assertTrue(self.reasons(active=True))

    def test_unavailable_playback_state_fails_closed(self):
        self.resources.value['playback_error']='Cannot check Jellyfin playback'
        self.assertIn('playback',' '.join(self.reasons()))

    def test_playback_gates_start_but_never_interrupts_active_processing(self):
        self.resources.config.value['pause_during_playback'] = True
        for playing in (r'C:\different-movie.mp4', r'C:\disposable-test.mp4'):
            with self.subTest(playing=playing):
                self.resources.value['playback'] = [norm(playing)]
                self.assertIn('playback', ' '.join(self.reasons()))
                self.assertEqual(self.reasons(active=True), [])

    def test_playback_monitor_failure_does_not_interrupt_active_processing(self):
        self.resources.value['playback_error'] = 'Cannot check Jellyfin playback'
        self.assertEqual(self.reasons(active=True), [])

    def test_disabled_start_gate_allows_other_movies_but_protects_source(self):
        self.resources.config.value['pause_during_playback'] = False
        self.resources.value['playback'] = [norm(r'C:\different-movie.mp4')]
        self.assertEqual(self.reasons(), [])
        self.resources.value['playback'] = [norm(r'C:\disposable-test.mp4')]
        self.assertIn('playback', ' '.join(self.reasons()))

    def test_force_allows_start_during_any_playback(self):
        self.resources.config.value.update(force_transcode_during_playback=True, pause_during_playback=True)
        for path in (r'C:\different-movie.mp4', r'C:\disposable-test.mp4'):
            self.resources.value['playback'] = [norm(path)]
            self.assertEqual(self.reasons(), [])

    def test_force_does_not_bypass_resource_guards(self):
        self.resources.config.value['force_transcode_during_playback'] = True
        self.resources.value['ram_free_gb'] = 0.1
        self.assertIn('Low available system memory', self.reasons())


if __name__=='__main__':unittest.main(verbosity=2)

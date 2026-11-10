"""Destructive-path tests use ONLY disposable generated media in a TemporaryDirectory."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

from core import BASE, Config, Inventory, Store, fingerprint, matching_rules, norm
from worker import Cancelled, WaitForResources, Worker, atomic_replace, file_hash


class IdleJellyfin:
    def playback_paths(self):
        return []

    def refresh(self):
        pass


class IdleResources:
    def reasons(self, *args, **kwargs):
        return []


class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='forge-tests-')
        cls.base = Path(cls.temp.name)
        cls.original = cls.base / 'fixture.mp4'
        cls.ffmpeg = Config().get()['ffmpeg']
        result = subprocess.run([cls.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                                 '-f', 'lavfi', '-i', 'anullsrc=channel_layout=5.1:sample_rate=48000', '-t', '2',
                                 '-c:v', 'libx265', '-pix_fmt', 'yuv420p10le', '-x265-params', 'pools=1:frame-threads=1',
                                 '-tag:v', 'hev1', '-c:a', 'aac', '-metadata:s:a:0', 'language=eng', str(cls.original)],
                                capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace'))
        cls.precise_source = cls.base / 'precise-clock.mp4'
        result = subprocess.run([cls.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24000/1001',
                                 '-vf', 'settb=1/1200000,setpts=(62220+N)*50049.6', '-frames:v', '720',
                                 '-c:v', 'libx265', '-pix_fmt', 'yuv420p10le', '-x265-params', 'pools=1:frame-threads=1',
                                 '-enc_time_base:v', 'filter', '-fps_mode:v', 'passthrough', '-video_track_timescale', '1200000',
                                 '-tag:v', 'hev1', str(cls.precise_source)], capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace'))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.test_dir = self.base / self.id().split('.')[-1]
        self.test_dir.mkdir()
        settings = Config().get()
        settings.update(stable_seconds=30, max_attempts=3, auto_queue=False)
        config_path = self.test_dir / 'config.json'
        config_path.write_text(json.dumps(settings))
        self.config = Config(config_path)
        self.store = Store(self.test_dir / 'test.db')
        self.stop = threading.Event()
        self.jellyfin = IdleJellyfin()
        self.inventory = Inventory(self.config, self.store, self.jellyfin, self.stop)
        self.inventory.roots = [{'library': 'TEST ONLY', 'path': str(self.test_dir)}]
        self.worker = Worker(self.config, self.store, self.inventory, IdleResources(), self.jellyfin, self.stop)
        self.worker.log_dir = self.test_dir
        self.worker.encoder = {'tested': True, 'available': True, 'error': None,
                               'nvdec_available': True, 'decoder_error': None}
        self.path = self.test_dir / 'disposable.mp4'
        shutil.copyfile(self.original, self.path)
        os.utime(self.path, (time.time()-300, time.time()-300))
        self.inventory.ingest(self.path, 'TEST ONLY')
        self.media = self.store.one('SELECT * FROM media')

    def queue(self):
        job_id = self.worker.enqueue(self.media['id'])
        return self.store.one('SELECT * FROM jobs WHERE id=?', (job_id,))

    def receipt(self, job, target=None):
        temp, expected_target = self.worker.paths(job)
        shutil.copyfile(self.original, temp)
        with temp.open('ab') as stream:
            stream.write(b'journal-recovery-fixture')
        self.store.job(job['id'], state='committing', temp=str(temp), target=str(target or expected_target),
                       output_size=temp.stat().st_size, output_hash=file_hash(temp), validation=json.dumps({'full_decode': True}))
        return self.store.one('SELECT * FROM jobs WHERE id=?', (job['id'],))

    def test_real_nvenc_replacement_preserves_surround_and_validates(self):
        old_hash = file_hash(self.path)
        job = self.queue()
        self.worker.execute(job)
        result = self.store.one('SELECT * FROM jobs WHERE id=?', (job['id'],))
        self.assertEqual(result['state'], 'completed')
        self.assertNotEqual(file_hash(self.path), old_hash)
        probe, info = self.inventory.probe(self.path)
        self.assertEqual(info['codec'], 'hevc')
        self.assertEqual(info['bit_depth'], 8)
        self.assertEqual(info['codec_tag'], 'hvc1')
        self.assertEqual(next(s for s in probe['streams'] if s['codec_type']=='audio')['channels'], 6)
        self.assertTrue(json.loads(result['validation'])['full_decode'])
        self.assertEqual(file_hash(self.path), result['output_hash'])
        self.assertFalse(self.worker.paths(job)[0].exists())
        self.assertFalse((self.path.parent/f'.forge-{job["id"]}.original.mp4').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing violation')
    def test_locked_commit_keeps_verified_output_and_retries_without_encoding(self):
        import ctypes
        from ctypes import wintypes
        job = self.receipt(self.queue())
        source_hash = file_hash(self.path)
        pending = self.worker.safe_temp(job)
        pending_hash = file_hash(pending)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(self.path), 0x80000000, 1, None, 3, 0x80, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            with self.assertRaises(WaitForResources):
                self.worker.commit(job)
            self.worker.cleanup_partial(job)
            self.assertEqual(file_hash(self.path), source_hash)
            self.assertEqual(file_hash(pending), pending_hash)
        finally:
            kernel.CloseHandle(handle)
        self.worker.commit(job)
        self.assertEqual(file_hash(self.path), pending_hash)
        self.assertEqual(self.store.one('SELECT state FROM jobs WHERE id=?', (job['id'],))['state'], 'completed')

    def test_torrent_connection_failure_blocks_encode_without_touching_source(self):
        from qbittorrent import TorrentHold
        class Unavailable:
            def guard(self, *args):
                raise TorrentHold('Local qBittorrent unavailable')
        self.worker.torrents = Unavailable()
        job = self.queue()
        before = file_hash(self.path)
        with self.assertRaisesRegex(WaitForResources, 'qBittorrent unavailable'):
            self.worker.execute(job)
        self.assertEqual(file_hash(self.path), before)
        self.assertFalse(self.worker.safe_temp(job).exists())

    def test_nvdec_pipeline_stays_on_gpu_and_preserves_10bit_when_requested(self):
        self.config.update({'video_decoder': 'nvdec', 'output_bit_depth': '10'})
        job = self.queue()
        self.worker.execute(job)
        _, info = self.inventory.probe(self.path)
        self.assertEqual(info['bit_depth'], 10)
        self.assertEqual(self.store.one('SELECT state FROM jobs WHERE id=?', (job['id'],))['state'], 'completed')

    def test_auto_falls_back_to_cpu_when_decoder_probe_is_unavailable(self):
        self.worker.encoder['nvdec_available'] = False
        job = self.queue()
        self.worker.execute(job)
        self.assertEqual(self.store.one('SELECT state FROM jobs WHERE id=?', (job['id'],))['state'], 'completed')

    def test_actual_frame_accounting_rejects_missing_or_error_counters(self):
        log = ('Input stream #0:0 (video): 50 packets read (1000 bytes); 48 frames decoded; 0 decode errors;\n'
               'Output stream #0:0 (video): 48 frames encoded; 48 packets muxed (900 bytes);')
        counts = self.worker.frame_accounting(log, 0, 0)
        self.assertEqual(counts['input_frames'], 48)
        self.assertEqual(counts['output_frames'], 48)
        with self.assertRaisesRegex(RuntimeError, 'Cannot verify'):
            self.worker.frame_accounting('', 0, 0)
        with self.assertRaisesRegex(RuntimeError, 'decode reported'):
            self.worker.frame_accounting(log.replace('0 decode errors', '1 decode errors'), 0, 0)

    def test_header_difference_requires_verified_discard_packets(self):
        job = self.queue()
        probe = {'streams': [{'index': 0, 'codec_type': 'video', 'codec_name': 'hevc', 'nb_frames': '50'}]}
        counts = {'input_packets': 50, 'input_frames': 48, 'input_errors': 0, 'output_frames': 48}
        self.worker.source_packet_counts = lambda *_: (50, 2)
        receipt = self.worker.source_frame_receipt(job, probe, counts)
        self.assertEqual(receipt['source_decoded_frames'], 48)
        self.assertEqual(receipt['source_discard_packets'], 2)
        self.worker.source_packet_counts = lambda *_: (50, 1)
        with self.assertRaisesRegex(RuntimeError, 'Unexplained source frame count'):
            self.worker.source_frame_receipt(job, probe, counts)
        with self.assertRaisesRegex(RuntimeError, 'Encoded video frame count differs'):
            self.worker.source_frame_receipt(job, probe, {**counts, 'output_frames': 47})
        self.assertTrue(self.path.exists())

    def test_real_mp4_preroll_is_accounted_without_dropping_playable_frames(self):
        edited = self.test_dir / 'preroll.mp4'
        result = subprocess.run([self.ffmpeg, '-v', 'error', '-ss', '0.125', '-i', str(self.original),
                                 '-t', '1.5', '-map', '0', '-c', 'copy', str(edited)], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        os.utime(edited, (time.time()-300, time.time()-300))
        self.inventory.ingest(edited, 'TEST ONLY')
        self.media = self.store.one('SELECT * FROM media WHERE path=?', (str(edited),))
        job = self.queue()
        self.worker.execute(job)
        saved = self.store.one('SELECT * FROM jobs WHERE id=?', (job['id'],))
        self.assertEqual(saved['state'], 'completed')
        receipt = json.loads(saved['validation'])
        self.assertGreater(receipt['source_discard_packets'], 0)
        self.assertEqual(receipt['source_header_frames'] - receipt['source_discard_packets'], receipt['source_decoded_frames'])
        self.assertEqual(receipt['source_decoded_frames'], receipt['encoded_frames'])
        self.assertEqual(receipt['encoded_frames'], receipt['decoded_frames'])

    def test_require_nvdec_never_silently_falls_back(self):
        from worker import WaitForResources
        self.config.update({'video_decoder': 'nvdec'})
        self.worker.encoder['nvdec_available'] = False
        job = self.queue()
        original_hash = file_hash(self.path)
        with self.assertRaisesRegex(WaitForResources, 'NVDEC is unavailable'):
            self.worker.execute(job)
        self.assertEqual(file_hash(self.path), original_hash)

    def test_unsupported_gpu_codec_uses_cpu_in_auto(self):
        probe = {'streams': [{'codec_type': 'video', 'codec_name': 'av1', 'pix_fmt': 'yuv420p10le'}]}
        self.assertEqual(self.worker.decoder_for(probe, {'video_decoder': 'auto'}), 'cpu')
        with self.assertRaisesRegex(ValueError, 'outside supported NVDEC'):
            self.worker.decoder_for(probe, {'video_decoder': 'nvdec'})

    def test_real_mkv_conversion_removes_original_only_after_validation(self):
        self.config.update({'output_container':'mkv'})
        job = self.queue()
        self.worker.execute(job)
        result = self.store.one('SELECT * FROM jobs WHERE id=?', (job['id'],))
        self.assertEqual(result['state'],'completed')
        self.assertFalse(self.path.exists())
        self.assertTrue(self.path.with_suffix('.mkv').exists())

    def test_precise_variable_frame_timestamps_survive_nvenc_and_validation(self):
        # Real MP4 fixture with alternating 50049/50050-tick frame intervals.
        # Rounding these to the nominal 24000/1001 FPS clock caused DTS collisions
        # in the reported movie. Compare actual presentation times, not just flags.
        from fractions import Fraction
        probe, info = self.inventory.probe(self.precise_source)
        job = {'path': str(self.precise_source), 'options': json.dumps(self.worker.options())}
        output = self.test_dir / 'precise-output.mp4'
        args = self.worker.command(job, probe, info, output)
        args.insert(args.index('-i'), '-copyts')  # Keep the late timeline of the regression fixture.
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

        def presentation_times(path):
            p = subprocess.run([self.config.get()['ffprobe'], '-v', 'error', '-select_streams', 'v:0',
                                '-show_streams', '-show_packets', '-show_entries', 'stream=time_base:packet=pts',
                                '-of', 'json', str(path)], capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr)
            data = json.loads(p.stdout)
            clock = Fraction(data['streams'][0]['time_base'])
            values = sorted(Fraction(packet['pts']) * clock for packet in data['packets'])
            return [value - values[0] for value in values]

        original_times = presentation_times(self.precise_source)
        self.assertEqual(len(original_times), 720)
        self.assertEqual(presentation_times(output), original_times)
        args = self.worker.validation_command(output)
        args.insert(args.index('-i'), '-copyts')
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('non monotonically', result.stderr.lower())
        self.assertIn('frame=720', result.stdout)

    def test_rules_match_only_observed_combination(self):
        info = json.loads(self.media['info'])
        rules = self.config.get()['rules']
        self.assertTrue(matching_rules(info, rules))
        for key,value in [('container','mkv'),('codec_tag','hvc1'),('profile','Main'),('pixel_format','yuv420p')]:
            self.assertFalse(matching_rules({**info,key:value},rules))
        self.assertFalse(matching_rules(info,[]))

    def test_tampered_output_never_removes_original(self):
        job = self.receipt(self.queue())
        original_hash = file_hash(self.path)
        temp = self.worker.paths(job)[0]
        temp.write_bytes(b'broken output')
        with self.assertRaisesRegex(RuntimeError,'missing or changed'):
            self.worker.commit(job)
        self.assertEqual(file_hash(self.path),original_hash)

    def test_changed_source_never_removed_during_commit(self):
        job=self.receipt(self.queue())
        with self.path.open('ab') as stream: stream.write(b'new data')
        changed_hash=file_hash(self.path)
        with self.assertRaisesRegex(RuntimeError,'changed'):
            self.worker.commit(job)
        self.assertEqual(file_hash(self.path),changed_hash)

    def test_existing_destination_is_never_overwritten(self):
        self.config.update({'output_container':'mkv'})
        job=self.queue()
        target=self.path.with_suffix('.mkv')
        target.write_bytes(b'existing unrelated file')
        original_hash=file_hash(self.path)
        with self.assertRaisesRegex(RuntimeError,'already exists'):
            self.worker.execute(job)
        self.assertEqual(target.read_bytes(),b'existing unrelated file')
        self.assertEqual(file_hash(self.path),original_hash)

    def test_reboot_recovers_incomplete_job_without_touching_original(self):
        job=self.queue()
        temp,_=self.worker.paths(job)
        temp.write_bytes(b'incomplete')
        self.store.job(job['id'],state='encoding',attempts=1,temp=str(temp))
        original_hash=file_hash(self.path)
        self.worker.recover()
        result=self.store.one('SELECT * FROM jobs WHERE id=?',(job['id'],))
        self.assertEqual(result['state'],'waiting')
        self.assertEqual(file_hash(self.path),original_hash)
        self.worker.execute(result)
        self.assertEqual(self.store.one('SELECT state FROM jobs WHERE id=?',(job['id'],))['state'],'completed')

    def test_reboot_finishes_promoted_cross_container_journal(self):
        self.config.update({'output_container':'mkv'})
        job=self.receipt(self.queue())
        temp,target=self.worker.paths(job)
        # Simulate the OS promotion having succeeded, then power loss before deletion / DB completion.
        os.rename(temp,target)
        self.worker.commit(job)
        self.assertFalse(self.path.exists())
        self.assertTrue(target.exists())
        self.assertEqual(self.store.one('SELECT state FROM jobs WHERE id=?',(job['id'],))['state'],'completed')
        self.worker.commit(self.store.one('SELECT * FROM jobs WHERE id=?',(job['id'],)))
        self.assertTrue(target.exists())  # Idempotent recovery.

    def test_reboot_recognizes_same_path_already_promoted(self):
        job=self.receipt(self.queue())
        temp,target=self.worker.paths(job)
        os.replace(temp,target)
        self.worker.commit(job)
        self.assertEqual(self.store.one('SELECT state FROM jobs WHERE id=?',(job['id'],))['state'],'completed')

    def test_reboot_cleans_rollback_only_after_verifying_promoted_output(self):
        job=self.receipt(self.queue())
        temp,target=self.worker.paths(job)
        backup=self.path.parent/f'.forge-{job["id"]}.original.mp4'
        atomic_replace(self.path,temp,backup)
        self.assertTrue(backup.exists())
        self.worker.commit(job)
        self.assertFalse(backup.exists())
        self.assertEqual(file_hash(target),job['output_hash'])

    def test_real_encoder_health_probe(self):
        self.worker.test_encoder()
        self.assertTrue(self.worker.encoder['available'],self.worker.encoder['error'])
        self.assertTrue(self.worker.encoder['nvdec_available'],self.worker.encoder['decoder_error'])
        receipt = json.loads((self.test_dir / 'gpu-test-result.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['process_id'], os.getpid())
        self.assertTrue(receipt['status']['nvdec_available'])

    def test_missing_validation_receipt_never_allows_deletion(self):
        job=self.queue()
        with self.assertRaisesRegex(RuntimeError,'validation receipt'):
            self.worker.commit(job)
        self.assertTrue(self.path.exists())

    def test_cancel_keeps_original(self):
        job=self.queue()
        original_hash=file_hash(self.path)
        self.worker.cancel(job['id'])
        with self.assertRaises(Cancelled):
            self.worker.execute(job)
        self.assertEqual(file_hash(self.path),original_hash)

    def test_failed_validation_never_enters_commit(self):
        job=self.queue()
        original_hash=file_hash(self.path)
        self.worker.validate_structure=lambda *_: (_ for _ in ()).throw(RuntimeError('injected validation failure'))
        with self.assertRaisesRegex(RuntimeError,'injected validation failure'):
            self.worker.execute(job)
        self.assertEqual(file_hash(self.path),original_hash)
        result=self.store.one('SELECT * FROM jobs WHERE id=?',(job['id'],))
        self.assertIsNone(result['output_hash'])
        self.assertNotEqual(result['state'],'completed')

    def test_wrong_temporary_path_is_rejected(self):
        job=self.receipt(self.queue())
        other=self.test_dir/'never-delete.txt';other.write_text('keep me')
        job['temp']=str(other)
        with self.assertRaisesRegex(RuntimeError,'Temporary path'):
            self.worker.commit(job)
        self.assertEqual(other.read_text(),'keep me')

    def test_outside_library_is_rejected(self):
        self.assertFalse(self.inventory.allowed(self.original))


if __name__=='__main__':
    unittest.main(verbosity=2)

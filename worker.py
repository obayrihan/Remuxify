"""NVENC worker. An original can be replaced only through the validated commit journal."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import tempfile
import time
import uuid

import psutil

from core import ACTIVE, CREATE_FLAGS, DATA, LOG, fingerprint, matching_rules, norm
from qbittorrent import TorrentHold


class WaitForResources(Exception):
    pass


class Cancelled(Exception):
    pass


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def winpath(path):
    text = str(Path(path).absolute())
    return text if text.startswith('\\\\?\\') else '\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text


def atomic_replace(source, replacement, backup):
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.ReplaceFileW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                        wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
        kernel.ReplaceFileW.restype = wintypes.BOOL
        if not kernel.ReplaceFileW(winpath(source), winpath(replacement), winpath(backup), 0, None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.link(source, backup)
        os.replace(replacement, source)


class SourceLease:
    """Deny concurrent writers on Windows while allowing read and atomic replacement."""
    def __init__(self, path, replacement=False):
        self.path, self.handle = str(path), None
        self.replacement = replacement

    def __enter__(self):
        if os.name == 'nt':
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                           ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            access = 0x80000000 | (0x10000 if self.replacement else 0)
            self.handle = kernel.CreateFileW(winpath(self.path), access, 1 | 4, None, 3, 0x80, None)
            if self.handle == ctypes.c_void_p(-1).value:
                self.handle = None
                raise WaitForResources('Waiting for another application to release the file; original retained')
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(self.handle)


def retain_child_job():
    """Windows Job Object kills FFmpeg children if this process crashes or is terminated."""
    if os.name != 'nt':
        return None
    class BASIC(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(k, ctypes.c_ulonglong) for k in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
    class EXTENDED(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', BASIC), ('IoInfo', IO), ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    handle = kernel.CreateJobObjectW(None, None)
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        raise RuntimeError(f'Could not enable child-process crash protection ({ctypes.get_last_error()})')
    return handle  # Keep open for the lifetime of the application. It is not inherited.


class Worker:
    def __init__(self, config, store, inventory, resources, jellyfin, stop):
        self.config, self.store, self.inventory = config, store, inventory
        self.resources, self.jellyfin, self.stop = resources, jellyfin, stop
        self.lock = threading.RLock()
        self.cancelled = set()
        self.current = None
        self.encoder = {'tested': False, 'available': False, 'error': None,
                        'nvdec_available': False, 'decoder_error': None}
        self.log_dir = DATA
        self.torrents = None  # Wired by the service; disposable worker tests stay isolated.

    def options(self):
        c = self.config.get()
        return {k: c[k] for k in ('output_container', 'output_bit_depth', 'audio_mode', 'video_decoder', 'cq', 'preset', 'max_video_mbps')}

    def enqueue(self, media_id):
        with self.lock:
            media = self.store.one('SELECT * FROM media WHERE id=? AND present=1', (media_id,))
            if not media or not media['probe'] or media['error']:
                raise ValueError('Select a stable, readable media file')
            if not self.inventory.allowed(media['path']) or fingerprint(media['path']) != json.loads(media['signature']):
                self.inventory.request_scan()
                raise ValueError('File changed or is outside a current library; scan again')
            existing = self.store.one("SELECT id FROM jobs WHERE media_id=? AND state IN ('queued','waiting','encoding','validating','committing')", (media_id,))
            if existing:
                return existing['id']
            info, probe = json.loads(media['info']), json.loads(media['probe'])
            self.check_supported(info, probe, self.options())
            job_id = uuid.uuid4().hex
            with self.store.connect() as db:
                db.execute('''INSERT INTO jobs(id,media_id,path,state,created,updated,signature,original_size,options,phase)
                              VALUES (?,?,?,'queued',?,?,?,?,?,'Awaiting preflight')''',
                           (job_id, media_id, media['path'], time.time(), time.time(), media['signature'], media['size'], json.dumps(self.options())))
            self.store.event(f'Queued {Path(media["path"]).name}', job_id=job_id)
            return job_id

    def check_supported(self, info, probe, options):
        if info['hdr']:
            raise ValueError('HDR / Dolby Vision needs a dedicated profile; original protected. Provide a sample before converting this file.')
        if info['interlaced']:
            raise ValueError('Interlaced video needs a dedicated deinterlace profile; original protected.')
        videos = [s for s in probe.get('streams', []) if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
        if len(videos) != 1:
            raise ValueError('Files with multiple timed video streams need a dedicated profile')
        if any(s.get('codec_type') not in ('video', 'audio', 'subtitle', 'attachment') for s in probe.get('streams', [])):
            raise ValueError('This file has data or unknown streams that cannot be safely preserved')
        if options['output_container'] == 'mp4':
            if any(s.get('codec_type') == 'attachment' for s in probe['streams']):
                raise ValueError('MP4 cannot preserve these attachments; choose MKV')
            if any(s.get('codec_type') == 'subtitle' and s.get('codec_name') not in ('subrip', 'ass', 'ssa', 'webvtt', 'mov_text', 'text') for s in probe['streams']):
                raise ValueError('MP4 cannot preserve image subtitles; choose MKV')
        if info['bit_depth'] > 10 or info['width'] > 8192 or info['height'] > 8192:
            raise ValueError('Video exceeds the supported NVENC profile')
        if options.get('video_decoder') == 'nvdec' and not self.nvdec_input_supported(probe):
            raise ValueError('This input is outside the supported NVDEC profiles; choose Auto or CPU decoding')

    @staticmethod
    def nvdec_input_supported(probe):
        video = next((s for s in probe.get('streams', []) if s.get('codec_type') == 'video'
                      and not s.get('disposition', {}).get('attached_pic')), {})
        codec, pixel = video.get('codec_name'), video.get('pix_fmt')
        # Conservative hardware-decoding profiles. AV1,
        # H.264 High 10, 4:2:2 and other layouts retain the software path in Auto.
        if codec == 'h264':
            return pixel in ('yuv420p', 'yuvj420p', 'nv12')
        return codec in ('hevc', 'vp9') and pixel in ('yuv420p', 'yuvj420p', 'nv12', 'yuv420p10le', 'p010le')

    def decoder_for(self, probe, options):
        mode = options.get('video_decoder', 'auto')
        if mode == 'cpu':
            return 'cpu'
        supported = self.nvdec_input_supported(probe)
        available = self.encoder.get('nvdec_available', False)
        if supported and available:
            return 'nvdec'
        if mode == 'nvdec':
            if not supported:
                raise ValueError('Input is outside supported NVDEC profiles')
            raise WaitForResources('NVDEC is unavailable; run the GPU test or select Auto/CPU decoding')
        return 'cpu'

    def estimate(self, media, options):
        probe, info = json.loads(media['probe']), json.loads(media['info'])
        audio_bits = sum(int(s.get('bit_rate') or 1536000) for s in probe['streams'] if s.get('codec_type') == 'audio')
        # A rate ceiling makes a conservative, explainable upper estimate possible.
        return int(info['duration'] * (options['max_video_mbps'] * 1e6 + audio_bits) / 8 * 1.15 + 128 * 1024**2)

    def paths(self, job):
        source = Path(job['path'])
        ext = json.loads(job['options'])['output_container']
        return source.parent / f'.forge-{job["id"]}.part.{ext}', source.with_suffix('.' + ext)

    def safe_temp(self, job):
        expected, _ = self.paths(job)
        if job.get('temp') and norm(job['temp']) != norm(expected):
            raise RuntimeError('Temporary path does not match this job')
        return expected

    def cleanup_partial(self, job):
        if job.get('output_hash') or job['state'] == 'committing':
            return
        try:
            temp = self.safe_temp(job)
            if temp.exists():
                temp.unlink()
        except OSError:
            self.store.event('Could not remove an incomplete job-owned temporary file; original remains intact', 'warning', job['id'])

    def cancel(self, job_id):
        with self.lock:
            job = self.store.one('SELECT * FROM jobs WHERE id=?', (job_id,))
            if not job or job['state'] not in ACTIVE:
                raise ValueError('Job is no longer active')
            if job['state'] == 'committing':
                raise ValueError('Verified replacement is being committed; wait for completion')
            self.cancelled.add(job_id)
            if job['state'] in ('queued', 'waiting'):
                self.store.job(job_id, state='cancelled', phase='Cancelled; original kept', error=None)
                self.cleanup_partial(job)
            self.store.event('Cancellation requested; original will be kept', job_id=job_id)

    def retry(self, job_id):
        job = self.store.one('SELECT * FROM jobs WHERE id=?', (job_id,))
        if not job or job['state'] not in ('failed', 'cancelled'):
            raise ValueError('Only failed or cancelled jobs can be retried')
        if job['output_hash'] and job['validation'] and json.loads(job['validation']).get('full_decode'):
            with self.lock:
                existing = self.store.one("SELECT id FROM jobs WHERE media_id=? AND id<>? AND state IN ('queued','waiting','encoding','validating','committing')", (job['media_id'], job_id))
                if existing:
                    raise ValueError('This media already has an active job')
                self.store.job(job_id, state='committing', next_attempt=0, error=None, phase='Recovering verified replacement')
            return job_id
        # Start a new journal instead of mutating a completed attempt.
        return self.enqueue(job['media_id'])

    def test_encoder(self):
        from core import run
        try:
            result = run([self.config.get()['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'color=size=320x180:rate=24',
                          '-t', '0.25', '-c:v', 'hevc_nvenc', '-preset', 'p4', '-f', 'null', '-'], 45)
            status = {'tested': True, 'available': result.returncode == 0,
                      'error': result.stderr[-1500:] if result.returncode else None,
                      'nvdec_available': False, 'decoder_error': None}
            if status['available']:
                try:
                    with tempfile.TemporaryDirectory(prefix='forge-gpu-test-') as folder:
                        fixture = Path(folder) / 'nvdec-probe.mkv'
                        ffmpeg = self.config.get()['ffmpeg']
                        encoded = run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=size=320x180:rate=24',
                                       '-t', '0.25', '-c:v', 'hevc_nvenc', '-pix_fmt', 'p010le', '-preset', 'p4', '-n', fixture], 30)
                        if encoded.returncode:
                            raise RuntimeError(encoded.stderr[-1200:])
                        decoded = run([ffmpeg, '-v', 'error', '-xerror', '-hwaccel:v:0', 'cuda',
                                       '-hwaccel_output_format:v:0', 'cuda', '-i', fixture,
                                       '-filter:v:0', 'scale_cuda=format=nv12:passthrough=0', '-c:v:0', 'hevc_nvenc',
                                       '-preset', 'p4', '-fps_mode:v:0', 'passthrough', '-enc_time_base:v:0', 'demux', '-f', 'null', '-'], 30)
                        status['nvdec_available'] = decoded.returncode == 0
                        status['decoder_error'] = decoded.stderr[-1500:] if decoded.returncode else None
                except Exception as exc:
                    status['decoder_error'] = str(exc)[:1500]
            self.encoder = status
        except Exception as exc:
            self.encoder = {'tested': True, 'available': False, 'error': str(exc)[:500],
                            'nvdec_available': False, 'decoder_error': None}
        # Record startup capabilities without needing a user's Jellyfin credentials.
        receipt = {'process_id': os.getpid(), 'checked_at': time.time(), 'status': self.encoder}
        target = self.log_dir / 'gpu-test-result.json'
        pending = target.with_name(f'gpu-test-result-{os.getpid()}-{threading.get_ident()}.tmp')
        try:
            with pending.open('w', encoding='utf-8') as stream:
                json.dump(receipt, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, target)
        except OSError:
            LOG.warning('Could not save GPU capability test receipt')

    def command(self, job, probe, info, temp):
        c, o = self.config.get(), json.loads(job['options'])
        video_ordinal = next(i for i, s in enumerate([s for s in probe['streams'] if s.get('codec_type') == 'video']) if not s.get('disposition', {}).get('attached_pic'))
        spec = f'v:{video_ordinal}'
        decoder = self.decoder_for(probe, o)
        depth = info['bit_depth'] if o['output_bit_depth'] == 'source' else int(o['output_bit_depth'])
        input_options = ['-threads', '2']
        if decoder == 'nvdec':
            input_options += ['-hwaccel:' + spec, 'cuda', '-hwaccel_output_format:' + spec, 'cuda',
                              '-extra_hw_frames:' + spec, '8']
        args = [c['ffmpeg'], '-hide_banner', '-nostdin', '-v', 'verbose', '-xerror', '-err_detect', 'explode',
                *input_options, '-i', job['path'], '-map', '0', '-map_metadata', '0', '-map_chapters', '0', '-c', 'copy',
                '-c:' + spec, 'hevc_nvenc', '-preset:' + spec, o['preset'], '-rc:' + spec, 'vbr',
                '-cq:' + spec, str(o['cq']), '-b:' + spec, '0', '-maxrate:' + spec, str(o['max_video_mbps']) + 'M',
                '-bufsize:' + spec, str(o['max_video_mbps'] * 2) + 'M', '-fps_mode:' + spec, 'passthrough',
                '-enc_time_base:' + spec, 'demux', '-max_error_rate', '0']
        if decoder == 'nvdec':
            # Convert bit depth on the GPU and feed CUDA frames directly to NVENC.
            # Allocate a separate output pool so NVENC cannot exhaust decoder surfaces.
            args += ['-filter:' + spec, 'scale_cuda=format=' + ('p010le' if depth == 10 else 'nv12') + ':passthrough=0']
        else:
            args += ['-pix_fmt:' + spec, 'p010le' if depth == 10 else 'yuv420p']
        if o['audio_mode'] in ('aac', 'aac_stereo'):
            args += ['-c:a', 'aac', '-b:a', '192k']
            if o['audio_mode'] == 'aac_stereo':
                args += ['-ac:a', '2']
        if o['output_container'] == 'mp4':
            args += ['-c:s', 'mov_text', '-tag:' + spec, 'hvc1', '-movflags', '+faststart']
        args += ['-progress', 'pipe:1', '-stats_period', '1', '-nostats', '-n', str(temp)]
        return args

    def validation_command(self, temp):
        # Preserve timestamp precision in the decode checker too. Its null muxer
        # must not quantize valid frame timestamps back onto a coarse FPS clock.
        return [self.config.get()['ffmpeg'], '-hide_banner', '-nostdin', '-v', 'verbose', '-xerror', '-err_detect', 'explode',
                '-threads', '2', '-i', str(temp), '-map', '0:v?', '-map', '0:a?',
                '-fps_mode:v', 'passthrough', '-enc_time_base:v', 'demux', '-max_error_rate', '0',
                '-progress', 'pipe:1', '-stats_period', '1', '-nostats', '-f', 'null', '-']

    def check_interrupt(self, job_id):
        if self.stop.is_set():
            raise WaitForResources('Worker stopped; original retained, retry after startup')
        if job_id in self.cancelled:
            raise Cancelled('Cancelled; original retained')

    @staticmethod
    def frame_accounting(log, input_index, output_index=None):
        """Read FFmpeg's actual per-stream EOF counters, not its progress estimate."""
        source = re.findall(r'Input stream #0:' + str(int(input_index)) +
                            r' \(video\):\s*(\d+) packets read[^\r\n]*?;\s*(\d+) frames decoded;\s*(\d+) decode errors;', log)
        if not source:
            raise RuntimeError('Cannot verify actual decoded input video frames; original kept')
        packets, frames, errors = map(int, source[-1])
        if errors or frames <= 0:
            raise RuntimeError(f'Input decode reported {errors} errors and {frames} frames; original kept')
        counts = {'input_packets': packets, 'input_frames': frames, 'input_errors': errors}
        if output_index is not None:
            output = re.findall(r'Output stream #0:' + str(int(output_index)) +
                                r' \(video\):\s*(\d+) frames encoded;', log)
            if not output or int(output[-1]) <= 0:
                raise RuntimeError('Cannot verify actual encoded video frames; original kept')
            counts['output_frames'] = int(output[-1])
        return counts

    def source_packet_counts(self, job, stream_index):
        from core import run
        self.check_interrupt(job['id'])
        result = run([self.config.get()['ffprobe'], '-v', 'error', '-select_streams', str(stream_index),
                      '-show_packets', '-show_entries', 'packet=flags', '-of', 'csv=p=0', job['path']], 300)
        self.check_interrupt(job['id'])
        if result.returncode:
            raise RuntimeError('Cannot verify source discard packets; original kept: ' + result.stderr[-800:])
        flags = [line.split(',', 1)[0].strip().strip('"') for line in result.stdout.splitlines() if line.strip()]
        if not flags or any(not re.fullmatch(r'[A-Za-z_]+', flag) for flag in flags):
            raise RuntimeError('Unexpected source packet accounting format; original kept')
        if any('C' in flag for flag in flags):
            raise RuntimeError('Source contains packets marked corrupt; original kept')
        return len(flags), sum('D' in flag for flag in flags)

    def source_frame_receipt(self, job, probe, counts):
        video = next(s for s in probe['streams'] if s.get('codec_type') == 'video'
                     and not s.get('disposition', {}).get('attached_pic'))
        actual = counts['input_frames']
        encoded = counts['output_frames']
        if actual != encoded:
            raise RuntimeError(f'Encoded video frame count differs: input decoded {actual}, output encoded {encoded}; original kept')
        header = int(video['nb_frames']) if str(video.get('nb_frames', '')).isdigit() and int(video['nb_frames']) > 0 else None
        receipt = {'source_header_frames': header, 'source_decoded_frames': actual,
                   'source_decode_errors': counts['input_errors'], 'source_discard_packets': 0}
        if header is not None and header != actual:
            # Only account for explicitly verified MP4 discard/preroll packets.
            # An unexplained mismatch remains a hard failure, never a tolerance.
            if Path(job['path']).suffix.lower() not in ('.mp4', '.m4v', '.mov') or video.get('codec_name') not in ('hevc', 'h264'):
                raise RuntimeError(f'Unexplained source frame count: header {header}, decoded {actual}; original kept')
            self.store.job(job['id'], phase='Verifying source discard packets', eta=None)
            packets, discarded = self.source_packet_counts(job, video['index'])
            if packets != header or packets != counts['input_packets'] or header - discarded != actual:
                raise RuntimeError(f'Unexplained source frame count: header {header}, decoded {actual}, packets {packets}, discarded {discarded}; original kept')
            receipt['source_discard_packets'] = discarded
            self.store.event(f'Source header includes {discarded} discarded video packets; requiring all {actual} playable frames in the output', job_id=job['id'])
        return receipt

    def process(self, args, job, duration, phase, estimate=0):
        log_path = self.log_dir / f'{job["id"]}-{phase}.log'
        lines = queue.Queue()
        finished = threading.Event()
        proc = None
        started = last_output = last_guard = time.monotonic()
        values = {}
        with log_path.open('w', encoding='utf-8') as log:
            try:
                proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=log, text=True,
                                        encoding='utf-8', errors='replace', creationflags=CREATE_FLAGS, bufsize=1)
                try:
                    psutil.Process(proc.pid).nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == 'nt' else 10)
                except psutil.Error:
                    pass
                def reader():
                    try:
                        for line in proc.stdout:
                            lines.put(line.rstrip())
                    finally:
                        finished.set()
                        lines.put(None)
                threading.Thread(target=reader, daemon=True).start()
                while True:
                    self.check_interrupt(job['id'])
                    try:
                        line = lines.get(timeout=1)
                    except queue.Empty:
                        line = ''
                    if line:
                        last_output = time.monotonic()
                        key, _, value = line.partition('=')
                        values[key] = value
                        if key == 'progress':
                            elapsed_media = float(values.get('out_time_us', 0)) / 1e6
                            elapsed = time.monotonic() - started
                            speed = elapsed_media / elapsed if elapsed > 0 else 0
                            eta = max(0, (duration - elapsed_media) / speed) if speed > 0 else None
                            self.store.job(job['id'], progress=min(99.9, max(0, elapsed_media / duration * 100)),
                                           fps=float(values.get('fps') or 0), speed=round(speed, 2), eta=eta,
                                           phase='Encoding HEVC · ' + job.get('_decoder_label', 'CPU → NVENC') if phase == 'encode' else 'Validating every video and audio frame')
                    if proc.poll() is not None and finished.is_set() and lines.empty():
                        break
                    if time.monotonic() - last_output > 180:
                        raise RuntimeError('FFmpeg stopped reporting progress for 3 minutes')
                    if time.monotonic() - last_guard > 5:
                        last_guard = time.monotonic()
                        temp = self.safe_temp(job)
                        used = temp.stat().st_size if temp.exists() else 0
                        remaining = max(0, estimate - used) if phase == 'encode' else 0
                        reasons = self.resources.reasons(job['path'], remaining, active=True)
                        if reasons:
                            raise WaitForResources('; '.join(reasons))
                code = proc.wait(timeout=10)
                if code:
                    log.flush()
                    tail = log_path.read_text(encoding='utf-8', errors='replace')[-2500:]
                    raise RuntimeError(f'FFmpeg {phase} failed (exit {code}): {tail}')
                log.flush()
                with log_path.open('rb') as details:
                    details.seek(max(0, log_path.stat().st_size - 64000))
                    tail = details.read().decode('utf-8', 'replace')
                index = job['_video_index']
                values['frame_accounting'] = self.frame_accounting(tail, index, index if phase == 'encode' else None)
                return values
            finally:
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=10)
                if proc and proc.stdout:
                    proc.stdout.close()

    def validate_structure(self, original, output, info, options, expected_video_frames=None):
        original_streams, streams = original['streams'], output['streams']
        if len(original_streams) != len(streams):
            raise RuntimeError('Output stream count differs; original kept')
        if len(original.get('chapters', [])) != len(output.get('chapters', [])):
            raise RuntimeError('Output chapter count differs; original kept')
        out_info = __import__('core').summarize(output, 'output.' + options['output_container'])
        expected_depth = info['bit_depth'] if options['output_bit_depth'] == 'source' else int(options['output_bit_depth'])
        if out_info['codec'] != 'hevc' or out_info['width'] != info['width'] or out_info['height'] != info['height'] or out_info['bit_depth'] != expected_depth:
            raise RuntimeError('Output video profile, resolution, or bit depth differs; original kept')
        if options['output_container'] == 'mp4' and out_info['codec_tag'] != 'hvc1':
            raise RuntimeError('Output is missing its hvc1 tag; original kept')
        if abs(out_info['duration'] - info['duration']) > max(0.25, min(2.0, info['duration'] * 0.0001)):
            raise RuntimeError('Output duration differs; original kept')
        for src, dst in zip(original_streams, streams):
            kind = src.get('codec_type')
            if kind != dst.get('codec_type'):
                raise RuntimeError('Output stream order or types differ; original kept')
            should_copy = kind == 'audio' and options['audio_mode'] == 'copy' or options['output_container'] == 'mkv' and kind in ('subtitle', 'attachment')
            if should_copy and src.get('codec_name') != dst.get('codec_name'):
                raise RuntimeError('A copied stream changed codec; original kept')
            expected_channels = 2 if options['audio_mode'] == 'aac_stereo' else src.get('channels')
            if kind == 'audio' and expected_channels != dst.get('channels'):
                raise RuntimeError('Audio channel count changed; original kept')
            lang = src.get('tags', {}).get('language')
            if lang and lang != 'und' and lang != dst.get('tags', {}).get('language'):
                raise RuntimeError('A stream language tag was lost; original kept')
            for flag in ('default', 'forced', 'attached_pic'):
                if src.get('disposition', {}).get(flag, 0) != dst.get('disposition', {}).get(flag, 0):
                    raise RuntimeError(f'A stream {flag} flag changed; original kept')
            if kind in ('video', 'audio') and src.get('duration') and dst.get('duration'):
                if abs(float(src['duration']) - float(dst['duration'])) > 1.0:
                    raise RuntimeError('An output stream is truncated; original kept')
            if kind == 'video' and str(dst.get('nb_frames', '')).isdigit():
                expected = int(src['nb_frames']) if str(src.get('nb_frames', '')).isdigit() else None
                if expected_video_frames is not None and not src.get('disposition', {}).get('attached_pic'):
                    expected = expected_video_frames
                if expected is not None and expected != int(dst['nb_frames']):
                    raise RuntimeError(f'Output video frame count differs: expected {expected}, found {dst["nb_frames"]}; original kept')
        return {'structure': True, 'duration': out_info['duration'], 'streams': len(streams), 'full_decode': False}

    def playback_guard(self, path):
        try:
            playing = self.jellyfin.playback_paths()
        except Exception:
            raise WaitForResources('Cannot confirm Jellyfin playback is idle before replacement')
        if norm(path) in playing:
            raise WaitForResources('The source is playing in Jellyfin; verified output retained until replacement is safe')

    def torrent_guard(self, job):
        if self.torrents is not None:
            try:
                self.torrents.guard(job['path'], lambda: self.check_interrupt(job['id']),
                                    lambda message: self.store.event(message, job_id=job['id']))
            except TorrentHold as exc:
                raise WaitForResources(str(exc)) from None

    def commit(self, job):
        try:
            return self._commit(job)
        except OSError as exc:
            if getattr(exc, 'winerror', None) in (32, 33):
                raise WaitForResources('Waiting for a file lock to clear; verified output retained for replacement without re-encoding') from None
            raise

    def _commit(self, job):
        """Replayable journal: output hash is persisted BEFORE any original is replaced."""
        source, temp = Path(job['path']), self.safe_temp(job)
        _, target = self.paths(job)
        if not job['output_hash'] or not job['validation'] or not json.loads(job['validation']).get('full_decode'):
            raise RuntimeError('Refusing replacement without a durable full-validation receipt')
        if job.get('target') and norm(job['target']) != norm(target):
            raise RuntimeError('Replacement target does not match job')
        same = norm(source) == norm(target)
        backup = source.parent / f'.forge-{job["id"]}.original{source.suffix}'
        # Covers a power loss after promotion, or after deletion but before DB completion.
        promoted = target.is_file() and target.stat().st_size == job['output_size'] and file_hash(target) == job['output_hash']
        if not promoted:
            if not source.exists() and backup.exists() and fingerprint(backup) == json.loads(job['signature']):
                # ReplaceFileW can leave the original under its backup name on an OS-level fault.
                os.rename(backup, source)
            if not source.is_file() or not self.inventory.allowed(source):
                raise RuntimeError('Original unavailable or library changed; no files removed')
            if fingerprint(source) != json.loads(job['signature']):
                raise RuntimeError('Original changed since encoding; no files removed')
            if not temp.is_file() or temp.stat().st_size != job['output_size'] or file_hash(temp) != job['output_hash']:
                raise RuntimeError('Verified temporary output is missing or changed; original kept')
            if not same and target.exists():
                raise RuntimeError('Output filename already exists; neither file was overwritten')
            if same and backup.exists():
                raise RuntimeError('A prior rollback file needs inspection; no files removed')
            self.torrent_guard(job)
            self.playback_guard(source)
            with SourceLease(source, replacement=True):
                if fingerprint(source) != json.loads(job['signature']):
                    raise RuntimeError('Original changed before replacement; original kept')
                with temp.open('r+b') as stream:
                    os.fsync(stream.fileno())
                if same:
                    atomic_replace(source, temp, backup)
                else:
                    # Windows rename refuses an existing target. Both names stay on the source volume.
                    if os.name == 'nt':
                        os.rename(temp, target)
                    else:
                        os.link(temp, target)
                        temp.unlink()
                if not same:
                    if fingerprint(source) != json.loads(job['signature']):
                        raise RuntimeError('Original changed during promotion; both files retained')
                    source.unlink()
        elif not same and source.exists():
            if not self.inventory.allowed(source) or fingerprint(source) != json.loads(job['signature']):
                raise RuntimeError('Recovery found a changed original; both files retained')
            self.torrent_guard(job)
            self.playback_guard(source)
            with SourceLease(source, replacement=True):
                if fingerprint(source) != json.loads(job['signature']):
                    raise RuntimeError('Original changed during recovery; both files retained')
                source.unlink()
        if same and backup.exists():
            if fingerprint(backup) != json.loads(job['signature']):
                raise RuntimeError('Rollback file changed; retained for inspection')
            if target.stat().st_size != job['output_size'] or file_hash(target) != job['output_hash']:
                raise RuntimeError('Promoted output changed; rollback original retained')
            with SourceLease(backup, replacement=True):
                if fingerprint(backup) != json.loads(job['signature']):
                    raise RuntimeError('Rollback file changed before cleanup; retained')
                backup.unlink()
        validation = json.loads(job['validation'])
        validation['output_signature'] = fingerprint(target)
        validation['sha256'] = job['output_hash']
        self.store.job(job['id'], state='completed', phase='Validated and replaced', progress=100, eta=0,
                       error=None, target=str(target), validation=json.dumps(validation))
        self.store.event(f'Validated and replaced {source.name}; only {target.name} retained', job_id=job['id'])
        self.inventory.request_scan()
        try:
            self.jellyfin.refresh()
        except Exception:
            self.store.event('Replacement complete; Jellyfin refresh deferred to its next scan', 'warning', job['id'])

    def execute(self, job):
        media = self.store.one('SELECT * FROM media WHERE id=?', (job['media_id'],))
        if not media or not Path(job['path']).is_file() or not self.inventory.allowed(job['path']):
            raise RuntimeError('Source is missing or no longer belongs to a Jellyfin library')
        if fingerprint(job['path']) != json.loads(job['signature']):
            raise RuntimeError('Source changed after this job was queued; scan and queue again')
        options = json.loads(job['options'])
        probe, info = json.loads(media['probe']), json.loads(media['info'])
        self.check_supported(info, probe, options)
        if not self.encoder['available']:
            raise WaitForResources('NVENC is unavailable; inspect encoder status')
        decoder = self.decoder_for(probe, options)
        estimate = self.estimate(media, options)
        reasons = self.resources.reasons(job['path'], estimate)
        if reasons:
            raise WaitForResources('; '.join(reasons))
        if time.time() - Path(job['path']).stat().st_mtime < self.config.get()['stable_seconds']:
            raise WaitForResources('Waiting for the file to finish downloading')
        temp, target = self.paths(job)
        if norm(target) != norm(job['path']) and target.exists():
            raise RuntimeError('Target filename already exists; original kept')
        self.torrent_guard(job)
        with SourceLease(job['path']):
            self.check_interrupt(job['id'])
            if temp.exists():
                # Only this exact job-owned partial output is disposable.
                self.safe_temp(job).unlink()
            self.store.job(job['id'], state='encoding', phase='Encoding HEVC', temp=str(temp), target=str(target),
                           attempts=job['attempts'] + 1, progress=0, error=None)
            job = self.store.one('SELECT * FROM jobs WHERE id=?', (job['id'],))
            job['_decoder_label'] = 'NVDEC → NVENC' if decoder == 'nvdec' else 'CPU → NVENC'
            job['_video_index'] = next(s['index'] for s in probe['streams'] if s.get('codec_type') == 'video'
                                       and not s.get('disposition', {}).get('attached_pic'))
            encoded_stats = self.process(self.command(job, probe, info, temp), job, info['duration'], 'encode', estimate)
            source_receipt = self.source_frame_receipt(job, probe, encoded_stats['frame_accounting'])
            self.store.job(job['id'], state='validating', progress=0, eta=None, phase='Inspecting output streams')
            output, _ = self.inventory.probe(temp)
            receipt = self.validate_structure(probe, output, info, options, source_receipt['source_decoded_frames'])
            receipt.update(source_receipt)
            # Decode every video and audio frame, including additional audio tracks.
            decoded_stats = self.process(self.validation_command(temp), job, info['duration'], 'validate')
            encoded_frames = encoded_stats['frame_accounting']['output_frames']
            decoded_frames = decoded_stats['frame_accounting']['input_frames']
            if encoded_frames <= 0 or encoded_frames != decoded_frames or encoded_frames != source_receipt['source_decoded_frames']:
                raise RuntimeError('Full decode frame count differs from the encode; original retained')
            receipt['encoded_frames'] = encoded_frames
            receipt['decoded_frames'] = decoded_frames
            receipt['full_decode'] = True
            self.check_interrupt(job['id'])
            if fingerprint(job['path']) != json.loads(job['signature']):
                raise RuntimeError('Source changed during encoding; original kept')
            self.store.job(job['id'], phase='Hashing validated output', eta=None)
            output_hash = file_hash(temp)
            # Commit is uncancellable from this durable journal boundary onward.
            with self.lock:
                self.check_interrupt(job['id'])
                self.store.job(job['id'], state='committing', phase='Committing verified replacement', progress=100,
                               output_hash=output_hash, output_size=temp.stat().st_size, validation=json.dumps(receipt))
        self.commit(self.store.one('SELECT * FROM jobs WHERE id=?', (job['id'],)))

    def recover(self):
        for job in self.store.rows("SELECT * FROM jobs WHERE state IN ('encoding','validating')"):
            state = 'waiting' if job['attempts'] < self.config.get()['max_attempts'] else 'failed'
            self.store.job(job['id'], state=state, progress=0, eta=None, next_attempt=0,
                           phase='Interrupted; original retained', error='Interrupted process; retry starts from the original')
            self.store.event('Recovered interrupted job; original retained', 'warning', job['id'])
        # Committing jobs retain their receipt and are replayed by the worker, never blindly retried.

    def auto_queue(self):
        c = self.config.get()
        if not c['auto_queue'] or not any(r['enabled'] for r in c['rules']):
            return
        for media in self.store.rows('SELECT * FROM media WHERE present=1 AND info IS NOT NULL AND error IS NULL'):
            if not matching_rules(json.loads(media['info']), c['rules']):
                continue
            history = self.store.rows('SELECT state,signature,validation FROM jobs WHERE media_id=?', (media['id'],))
            # Do not loop on outputs or re-enqueue failed inputs without an explicit retry.
            if any(h['signature'] == media['signature'] or (h['validation'] and json.loads(h['validation']).get('output_signature') == json.loads(media['signature'])) for h in history):
                continue
            try:
                self.enqueue(media['id'])
            except (ValueError, OSError):
                pass

    def loop(self):
        self.recover()
        self.test_encoder()
        last_auto = 0
        while not self.stop.is_set():
            job = None
            try:
                if time.time() - last_auto > 30:
                    self.auto_queue()
                    last_auto = time.time()
                if not self.config.get()['queue_paused']:
                    job = self.store.one("SELECT * FROM jobs WHERE state IN ('queued','waiting','committing') AND next_attempt<=? ORDER BY CASE state WHEN 'committing' THEN 0 ELSE 1 END,created LIMIT 1", (time.time(),))
                if job:
                    with self.lock:
                        latest = self.store.one('SELECT * FROM jobs WHERE id=?', (job['id'],))
                        if latest['state'] not in ('queued', 'waiting', 'committing'):
                            continue
                        self.current = job['id']
                    if job['state'] == 'committing':
                        self.commit(job)
                    else:
                        self.execute(job)
            except Cancelled as exc:
                self.cleanup_partial(self.store.one('SELECT * FROM jobs WHERE id=?', (job['id'],)))
                self.store.job(job['id'], state='cancelled', phase='Cancelled; original kept', error=str(exc), eta=None)
            except WaitForResources as exc:
                if job:
                    latest = self.store.one('SELECT * FROM jobs WHERE id=?', (job['id'],))
                    self.cleanup_partial(latest)
                    state = 'committing' if latest['state'] == 'committing' else 'waiting'
                    self.store.job(job['id'], state=state, phase=str(exc), next_attempt=time.time() + 30, eta=None)
            except Exception as exc:
                LOG.exception('Worker job failed')
                if job:
                    latest = self.store.one('SELECT * FROM jobs WHERE id=?', (job['id'],))
                    self.cleanup_partial(latest)
                    # A commit failure always needs inspection; never discard its journal or files.
                    retry = latest['state'] not in ('committing', 'queued', 'waiting') and latest['attempts'] < self.config.get()['max_attempts']
                    self.store.job(job['id'], state='waiting' if retry else 'failed', error=str(exc)[:3000],
                                   phase='Retry scheduled; original kept' if retry else 'Needs attention; files retained',
                                   next_attempt=time.time() + 300, eta=None)
                    self.store.event(str(exc)[:2000], 'error', job['id'])
            finally:
                self.current = None
            self.stop.wait(2)

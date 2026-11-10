"""Local library inventory, rules, telemetry, and durable queue storage."""
from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

import psutil
import requests
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from telemetry import WindowsDiskActivity, disk_io_sample

BASE = Path(__file__).resolve().parent
DATA = BASE / 'data'
DATA.mkdir(exist_ok=True)
LOG = logging.getLogger('forge')
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
EXTENSIONS = {'.mkv', '.mp4', '.m4v', '.avi', '.mov', '.ts', '.m2ts', '.mts', '.wmv', '.webm', '.mpg', '.mpeg', '.vob'}
ACTIVE = ('queued', 'waiting', 'encoding', 'validating', 'committing')
FIELDS = {'video_codecs', 'profiles', 'pixel_formats', 'containers', 'codec_tags', 'min_bit_depth', 'max_bit_depth', 'min_width', 'interlaced'}


def run(args, timeout=30):
    return subprocess.run([str(x) for x in args], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout,
                          creationflags=CREATE_FLAGS)


def fingerprint(path):
    s = Path(path).stat()
    return {'size': s.st_size, 'mtime_ns': s.st_mtime_ns, 'inode': s.st_ino, 'device': s.st_dev}


def is_reparse(path):
    return bool(getattr(Path(path).lstat(), 'st_file_attributes', 0) & 0x400) or Path(path).is_symlink()


def norm(path):
    return os.path.normcase(str(Path(path).resolve()))


class Config:
    def __init__(self, path=BASE / 'config.json'):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.value = json.loads(self.path.read_text(encoding='utf-8-sig'))
        self.value.setdefault('video_decoder', 'auto')
        self.value.setdefault('force_transcode_during_playback', False)

    def get(self):
        with self.lock:
            return copy.deepcopy(self.value)

    def update(self, changes):
        editable = {'output_container', 'output_bit_depth', 'audio_mode', 'video_decoder', 'cq', 'preset', 'max_video_mbps', 'min_free_gb',
                    'max_cpu_percent', 'max_gpu_percent', 'max_disk_percent', 'min_ram_gb',
                    'min_vram_mb', 'stable_seconds', 'pause_during_playback', 'auto_queue',
                    'queue_paused', 'rules', 'force_transcode_during_playback'}
        if set(changes) - editable:
            raise ValueError('Unknown settings')
        with self.lock:
            new = {**self.value, **changes}
            if new['output_container'] not in ('mkv', 'mp4') or new['preset'] not in ('p4', 'p5', 'p6'):
                raise ValueError('Unsupported container or preset')
            if new['output_bit_depth'] not in ('8', '10', 'source') or new['audio_mode'] not in ('copy', 'aac', 'aac_stereo'):
                raise ValueError('Unsupported video bit depth or audio mode')
            if new['video_decoder'] not in ('auto', 'nvdec', 'cpu'):
                raise ValueError('Unsupported video decoder')
            ranges = {'cq': (15, 35), 'max_video_mbps': (2, 100), 'min_free_gb': (1, 1000),
                      'max_cpu_percent': (10, 99), 'max_gpu_percent': (10, 99),
                      'max_disk_percent': (10, 100), 'min_ram_gb': (0.5, 128),
                      'min_vram_mb': (256, 16000), 'stable_seconds': (30, 3600)}
            for key, (low, high) in ranges.items():
                if type(new[key]) not in (int, float) or not low <= new[key] <= high:
                    raise ValueError(f'{key} must be between {low} and {high}')
            if int(new['cq']) != new['cq']:
                raise ValueError('CQ must be a whole number')
            for key in ('auto_queue', 'queue_paused', 'pause_during_playback', 'force_transcode_during_playback'):
                if type(new[key]) is not bool:
                    raise ValueError(f'{key} must be true or false')
            rules = new['rules']
            if not isinstance(rules, list) or len(rules) > 100:
                raise ValueError('Rules must be an array of at most 100 entries')
            ids = set()
            for rule in rules:
                if not isinstance(rule, dict) or set(rule) - {'id', 'name', 'enabled', 'conditions'}:
                    raise ValueError('A rule needs id, name, enabled and conditions')
                if not all(isinstance(rule.get(k), str) and 0 < len(rule[k]) <= 160 for k in ('id', 'name')):
                    raise ValueError('Every rule needs a short id and name')
                if rule['id'] in ids or type(rule.get('enabled')) is not bool:
                    raise ValueError('Rule IDs must be unique; enabled must be true or false')
                ids.add(rule['id'])
                cond = rule.get('conditions')
                if not isinstance(cond, dict) or not cond or set(cond) - FIELDS:
                    raise ValueError('Every rule must have supported conditions; empty rules are forbidden')
                for key, value in cond.items():
                    if key in ('min_bit_depth', 'max_bit_depth', 'min_width'):
                        if type(value) is not int or not 1 <= value <= 16384:
                            raise ValueError(f'{key} must be a positive integer')
                    elif key == 'interlaced':
                        if type(value) is not bool:
                            raise ValueError('interlaced must be true or false')
                    elif not isinstance(value, list) or not value or len(value) > 30 or not all(isinstance(x, str) and 0 < len(x) < 100 for x in value):
                        raise ValueError(f'{key} must be a nonempty array of short strings')
            temp = self.path.with_suffix('.tmp')
            with temp.open('w', encoding='utf-8') as f:
                json.dump(new, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)
            self.value = new
        return self.get()


class Store:
    def __init__(self, path=DATA / 'forge.db'):
        self.path = Path(path)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, library TEXT NOT NULL,
                size INTEGER, mtime_ns INTEGER, signature TEXT, probe TEXT, info TEXT,
                error TEXT, present INTEGER NOT NULL DEFAULT 1, updated REAL);
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, media_id INTEGER NOT NULL, path TEXT NOT NULL,
                state TEXT NOT NULL, created REAL, updated REAL, attempts INTEGER DEFAULT 0,
                next_attempt REAL DEFAULT 0, progress REAL DEFAULT 0, fps REAL DEFAULT 0,
                speed REAL DEFAULT 0, eta REAL, phase TEXT, error TEXT,
                temp TEXT, target TEXT, signature TEXT, output_hash TEXT, output_size INTEGER,
                original_size INTEGER, options TEXT, validation TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_live_job ON jobs(media_id)
                WHERE state IN ('queued','waiting','encoding','validating','committing');
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, time REAL, level TEXT, message TEXT, job_id TEXT);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    def rows(self, query, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, args)]

    def one(self, query, args=()):
        rows = self.rows(query, args)
        return rows[0] if rows else None

    def execute(self, query, args=()):
        with self.connect() as db:
            return db.execute(query, args).rowcount

    def job(self, job_id, **fields):
        fields['updated'] = time.time()
        return self.execute('UPDATE jobs SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?',
                            (*fields.values(), job_id))

    def event(self, message, level='info', job_id=None):
        LOG.log(logging.ERROR if level == 'error' else logging.INFO, '%s', message)
        with self.connect() as db:
            db.execute('INSERT INTO events(time,level,message,job_id) VALUES (?,?,?,?)',
                       (time.time(), level, message[:2500], job_id))
            db.execute('DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id),0)-3000 FROM events)')


def summarize(probe, path):
    streams = probe.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), {})
    pix = video.get('pix_fmt', '')
    depth = int(video.get('bits_per_raw_sample') or (12 if '12' in pix else 10 if '10' in pix or pix == 'p010le' else 8))
    side = video.get('side_data_list', [])
    hdr = video.get('color_transfer') in ('smpte2084', 'arib-std-b67') or any('dovi' in s.get('side_data_type', '').lower() for s in side)
    return {'codec': video.get('codec_name', 'unknown'), 'profile': video.get('profile', ''),
            'pixel_format': pix, 'bit_depth': depth, 'container': Path(path).suffix[1:].lower(),
            'codec_tag': video.get('codec_tag_string', ''), 'width': video.get('width', 0),
            'height': video.get('height', 0), 'interlaced': video.get('field_order', 'unknown') not in ('progressive', 'unknown'),
            'duration': float(probe.get('format', {}).get('duration') or video.get('duration') or 0),
            'hdr': hdr, 'audio': [s.get('codec_name', '') for s in streams if s.get('codec_type') == 'audio'],
            'subtitles': sum(s.get('codec_type') == 'subtitle' for s in streams)}


def matching_rules(info, rules):
    aliases = {'video_codecs': 'codec', 'profiles': 'profile', 'pixel_formats': 'pixel_format',
               'containers': 'container', 'codec_tags': 'codec_tag'}
    hits = []
    for rule in rules:
        cond = rule.get('conditions', {})
        if not rule.get('enabled') or not cond:
            continue
        ok = True
        for key, values in cond.items():
            if key in aliases:
                ok &= str(info.get(aliases[key], '')).lower() in [v.lower() for v in values]
            elif key == 'min_bit_depth':
                ok &= info.get('bit_depth', 0) >= values
            elif key == 'max_bit_depth':
                ok &= info.get('bit_depth', 0) <= values
            elif key == 'min_width':
                ok &= info.get('width', 0) >= values
            elif key == 'interlaced':
                ok &= info.get('interlaced') == values
            else:
                ok = False
        if ok:
            hits.append(rule['name'])
    return hits


class Jellyfin:
    def __init__(self, config):
        self.config = config

    def key(self):
        path = Path(self.config.get()['jellyfin_data']) / 'data/jellyfin.db'
        # Read the existing server-side key; never copy it into source, URLs, or logs.
        with sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, timeout=5) as db:
            row = db.execute('SELECT AccessToken FROM ApiKeys ORDER BY Id LIMIT 1').fetchone()
        if not row:
            raise RuntimeError('No Jellyfin API key is available')
        return row[0]

    def request(self, method, endpoint, token=None, **kwargs):
        return requests.request(method, self.config.get()['jellyfin_url'] + endpoint,
                                headers={'X-Emby-Token': token or self.key(),
                                         'Authorization': 'MediaBrowser Client="Media Forge", Device="Web", DeviceId="jellyfin-forge", Version="1.0"'},
                                timeout=12, **kwargs)

    def roots(self):
        # Use the API first; on server startup an XML fallback keeps inventory available.
        try:
            r = self.request('GET', '/Library/VirtualFolders')
            r.raise_for_status()
            return [{'library': x['Name'], 'path': p} for x in r.json() for p in x.get('Locations', [])]
        except Exception:
            roots = []
            for p in (Path(self.config.get()['jellyfin_data']) / 'root/default').glob('*/options.xml'):
                tree = ET.parse(p)
                for item in tree.findall('.//PathInfos/MediaPathInfo/Path'):
                    if item.text:
                        roots.append({'library': p.parent.name, 'path': item.text})
            if not roots:
                raise RuntimeError('Cannot discover Jellyfin library folders')
            return roots

    def playback_paths(self):
        r = self.request('GET', '/Sessions')
        r.raise_for_status()
        paths = []
        for session in r.json():
            item = session.get('NowPlayingItem')
            if item:
                path = item.get('Path')
                if not path:
                    raise RuntimeError('A Jellyfin playback session has no identifiable path')
                paths.append(norm(path))
        return paths

    def refresh(self):
        r = self.request('POST', '/Library/Refresh')
        r.raise_for_status()


class Inventory(FileSystemEventHandler):
    def __init__(self, config, store, jellyfin, stop):
        self.config, self.store, self.jellyfin, self.stop = config, store, jellyfin, stop
        self.roots = []
        self.status = {'scanning': False, 'last_scan': None, 'error': None, 'pending': 0}
        self.wake = threading.Event()
        self.full = threading.Event()
        self.full.set()
        self.lock = threading.Lock()
        self.dirty = set()
        self.observer = None
        self.watched = set()

    def on_any_event(self, event):
        if event.event_type not in ('created', 'modified', 'moved', 'deleted'):
            return
        if event.is_directory:
            self.full.set()
        else:
            with self.lock:
                for p in (event.src_path, getattr(event, 'dest_path', '')):
                    if p and Path(p).suffix.lower() in EXTENSIONS and not Path(p).name.startswith('.forge-'):
                        self.dirty.add(p)
        self.wake.set()

    def allowed(self, path, roots=None):
        p = Path(path)
        if is_reparse(p):
            return False
        resolved = p.resolve()
        for root in roots if roots is not None else self.roots:
            base = Path(root['path']).resolve()
            try:
                resolved.relative_to(base)
                # Refuse junctions and symlinks in every component below a library root.
                current = p
                while current != base and current != current.parent:
                    if is_reparse(current):
                        return False
                    current = current.parent
                return not is_reparse(base)
            except (ValueError, OSError):
                continue
        return False

    def probe(self, path):
        r = run([self.config.get()['ffprobe'], '-v', 'error', '-show_format', '-show_streams', '-show_chapters', '-of', 'json', path], 90)
        if r.returncode:
            raise RuntimeError(r.stderr[-1200:] or 'FFprobe could not read the file')
        probe = json.loads(r.stdout)
        info = summarize(probe, path)
        if not any(s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic') for s in probe.get('streams', [])) or info['duration'] <= 0:
            raise ValueError('No timed video stream was found')
        return probe, info

    def ingest(self, path, library):
        if Path(path).name.startswith('.forge-') or Path(path).suffix.lower() not in EXTENSIONS:
            return
        path = str(Path(path).resolve())
        if not self.allowed(path):
            return
        sig = fingerprint(path)
        old = self.store.one('SELECT * FROM media WHERE path=?', (path,))
        signature = json.dumps(sig, sort_keys=True)
        if old and old['signature'] == signature and old['probe']:
            if not old['present'] or old['library'] != library:
                self.store.execute('UPDATE media SET present=1,library=? WHERE id=?', (library, old['id']))
            return
        if self.store.one("SELECT id FROM jobs WHERE path=? AND state IN ('encoding','validating','committing')", (path,)):
            return
        if time.time() - Path(path).stat().st_mtime < self.config.get()['stable_seconds']:
            with self.lock:
                self.dirty.add(path)
            return
        error, probe, info = None, None, None
        try:
            probe, info = self.probe(path)
            if fingerprint(path) != sig:
                return
        except Exception as exc:
            error = str(exc)[:1200]
        with self.store.connect() as db:
            db.execute('''INSERT INTO media(path,library,size,mtime_ns,signature,probe,info,error,present,updated)
                        VALUES (?,?,?,?,?,?,?,?,1,?) ON CONFLICT(path) DO UPDATE SET
                        library=excluded.library,size=excluded.size,mtime_ns=excluded.mtime_ns,
                        signature=excluded.signature,probe=excluded.probe,info=excluded.info,
                        error=excluded.error,present=1,updated=excluded.updated''',
                       (path, library, sig['size'], sig['mtime_ns'], signature,
                        json.dumps(probe) if probe else None, json.dumps(info) if info else None, error, time.time()))

    def request_scan(self):
        self.full.set()
        self.wake.set()

    def discover(self):
        roots = self.jellyfin.roots()
        online = {norm(r['path']) for r in roots if Path(r['path']).is_dir() and not is_reparse(r['path'])}
        if online != self.watched:
            if self.observer:
                self.observer.stop()
                self.observer.join(timeout=5)
            self.observer = Observer()
            watching = set()
            for root in roots:
                p = norm(root['path'])
                if p in online and p not in watching:
                    try:
                        self.observer.schedule(self, root['path'], recursive=True)
                        watching.add(p)
                    except OSError as exc:
                        self.store.event(f'Watch unavailable for {root["path"]}: {exc}', 'warning')
            self.observer.start()
            self.watched = watching
        if roots != self.roots:
            self.full.set()
            for row in self.store.rows('SELECT id,path FROM media WHERE present=1'):
                within = False
                for root in roots:
                    try:
                        Path(row['path']).resolve().relative_to(Path(root['path']).resolve())
                        within = True
                        break
                    except ValueError:
                        continue
                if not within:
                    self.store.execute('UPDATE media SET present=0 WHERE id=?', (row['id'],))
        self.roots = roots

    def scan(self):
        self.status.update(scanning=True, error=None)
        try:
            for root in self.roots:
                if self.stop.is_set():
                    break
                base = Path(root['path'])
                if not base.is_dir() or is_reparse(base):
                    continue
                seen, errors = set(), []
                for folder, dirs, files in os.walk(base, onerror=errors.append, followlinks=False):
                    dirs[:] = [d for d in dirs if not is_reparse(Path(folder) / d)]
                    for name in files:
                        if self.stop.is_set():
                            return
                        p = Path(folder) / name
                        if p.suffix.lower() not in EXTENSIONS or name.startswith('.forge-'):
                            continue
                        seen.add(norm(p))
                        try:
                            self.ingest(p, root['library'])
                        except (OSError, ValueError) as exc:
                            self.status['error'] = str(exc)[:200]
                if not errors:
                    for row in self.store.rows('SELECT id,path FROM media WHERE library=? AND present=1', (root['library'],)):
                        try:
                            Path(row['path']).resolve().relative_to(base.resolve())
                            if norm(row['path']) not in seen:
                                self.store.execute('UPDATE media SET present=0 WHERE id=?', (row['id'],))
                        except ValueError:
                            pass
                else:
                    self.status['error'] = f'{base}: {errors[0]}'
            self.status['last_scan'] = time.time()
        finally:
            self.status['scanning'] = False

    def loop(self):
        last_scan = last_roots = 0
        while not self.stop.is_set():
            try:
                if time.time() - last_roots > 30:
                    self.discover()
                    last_roots = time.time()
                if self.full.is_set() or time.time() - last_scan > self.config.get()['scan_interval_seconds']:
                    self.full.clear()
                    self.scan()
                    last_scan = time.time()
                with self.lock:
                    dirty, self.dirty = self.dirty, set()
                for path in dirty:
                    if not Path(path).is_file():
                        self.store.execute('UPDATE media SET present=0 WHERE path=?', (str(Path(path).resolve()),))
                        continue
                    for root in self.roots:
                        try:
                            Path(path).resolve().relative_to(Path(root['path']).resolve())
                            self.ingest(path, root['library'])
                            break
                        except ValueError:
                            continue
                with self.lock:
                    self.status['pending'] = len(self.dirty)
            except Exception as exc:
                self.status['error'] = str(exc)[:250]
                LOG.exception('Inventory error')
            self.wake.wait(5)
            self.wake.clear()
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)


class Resources:
    def __init__(self, config, inventory, jellyfin, stop):
        self.config, self.inventory, self.jellyfin, self.stop = config, inventory, jellyfin, stop
        self.value = {'ready': False, 'gpu': None, 'disks': [], 'cpu': None, 'ram_free_gb': 0,
                      'disk_busy': None, 'disk_mbps': 0, 'disk_estimated': True,
                      'playback': [], 'playback_error': 'Not checked yet', 'sampled': 0}
        self.receipt_dir = DATA

    def loop(self):
        psutil.cpu_percent()
        prior, last = psutil.disk_io_counters(perdisk=True), time.monotonic()
        last_playback = 0
        native_disk, disk_error = None, None
        if os.name == 'nt':
            try:
                native_disk = WindowsDiskActivity()
            except OSError as exc:
                disk_error = str(exc)
                LOG.warning('Disk activity counter unavailable: %s', exc)
        last_receipt = 0
        while not self.stop.wait(5):
            try:
                current, now = psutil.disk_io_counters(perdisk=True), time.monotonic()
                disk_mbps, disk_load = disk_io_sample(prior, current, now - last)
                disk_activity = {}
                disk_source = 'I/O busy-time counter' if disk_load is not None else 'unavailable'
                if native_disk:
                    try:
                        disk_activity = native_disk.read()
                        disk_load = max(disk_activity.values())
                        disk_source, disk_error = 'Windows physical-disk active time', None
                    except OSError as exc:
                        disk_load, disk_error = None, str(exc)
                        disk_source = 'unavailable'
                prior, last = current, now
                gpu = None
                try:
                    r = run(['nvidia-smi', '--query-gpu=name,utilization.gpu,utilization.encoder,utilization.decoder,memory.free,memory.total', '--format=csv,noheader,nounits'], 8)
                    if r.returncode == 0:
                        parts = [x.strip() for x in r.stdout.strip().splitlines()[0].split(',')]
                        gpu = {'name': parts[0], 'utilization': float(parts[1]), 'encoder': float(parts[2]),
                               'decoder': float(parts[3]), 'free_mb': float(parts[4]), 'total_mb': float(parts[5])}
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
                disks, seen = [], set()
                for root in self.inventory.roots:
                    anchor = Path(root['path']).anchor
                    if anchor.lower() in seen:
                        continue
                    seen.add(anchor.lower())
                    try:
                        usage = psutil.disk_usage(root['path'])
                        disks.append({'path': anchor, 'free': usage.free, 'total': usage.total, 'online': True})
                    except OSError:
                        disks.append({'path': anchor, 'online': False, 'free': 0, 'total': 0})
                playback, playback_error = self.value['playback'], self.value['playback_error']
                if time.time() - last_playback > 10:
                    try:
                        playback = self.jellyfin.playback_paths()
                        playback_error = None
                    except Exception:
                        playback_error = 'Cannot check Jellyfin playback; queue is waiting safely'
                    last_playback = time.time()
                self.value = {'ready': True, 'cpu': psutil.cpu_percent(), 'ram_free_gb': psutil.virtual_memory().available / 1024**3,
                              'gpu': gpu, 'disks': disks, 'disk_busy': disk_load, 'disk_mbps': disk_mbps,
                              'disk_estimated': not bool(native_disk and disk_activity), 'disk_activity_by_device': disk_activity,
                              'disk_busy_source': disk_source, 'disk_busy_error': disk_error,
                              'playback': playback, 'playback_error': playback_error, 'sampled': time.time()}
                if time.time() - last_receipt >= 30:
                    # Local diagnostics omit playback paths and account/session information.
                    receipt = {k: self.value[k] for k in ('cpu', 'disk_busy', 'disk_mbps', 'disk_estimated',
                                                         'disk_activity_by_device', 'disk_busy_source', 'disk_busy_error', 'sampled')}
                    receipt['process_id'] = os.getpid()
                    pending = self.receipt_dir / f'resource-sample-{os.getpid()}.tmp'
                    try:
                        pending.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
                        os.replace(pending, self.receipt_dir / 'resource-sample.json')
                    except OSError:
                        LOG.warning('Could not save resource sample receipt')
                    last_receipt = time.time()
            except Exception:
                LOG.exception('Resource sampler error')
        if native_disk:
            native_disk.close()

    def reasons(self, path, required_bytes=0, active=False):
        c, r = self.config.get(), copy.deepcopy(self.value)
        reasons = []
        if not r['ready'] or time.time() - r['sampled'] > 25:
            return ['Waiting for a fresh resource sample']
        try:
            free = psutil.disk_usage(str(Path(path).parent)).free
            required = required_bytes + c['min_free_gb'] * 1024**3
            if free < required:
                reasons.append(f'Low storage: {free / 1024**3:.1f} GiB free; {required / 1024**3:.1f} GiB required including reserve')
        except OSError:
            reasons.append('Source disk is offline or inaccessible')
        if r['ram_free_gb'] < c['min_ram_gb']:
            reasons.append('Low available system memory')
        if not r['gpu']:
            reasons.append('NVIDIA telemetry is unavailable')
        elif r['gpu']['free_mb'] < c['min_vram_mb']:
            reasons.append('Low available GPU memory')
        if not active:
            if r['cpu'] > c['max_cpu_percent']:
                reasons.append('CPU is busy')
            if r['gpu'] and max(r['gpu']['utilization'], r['gpu']['encoder'], r['gpu'].get('decoder', 0)) > c['max_gpu_percent']:
                reasons.append('GPU is busy')
            if r.get('disk_busy') is None:
                reasons.append('Disk activity telemetry is unavailable')
            elif r['disk_busy'] > c['max_disk_percent']:
                reasons.append('Disk is busy' + (' (estimated activity)' if r.get('disk_estimated', True) else ' (active time)'))
        # Playback gates new work only. Never discard an active encode or
        # validation because a viewer starts watching (or monitoring fails).
        # The worker independently rechecks the exact source before replacement.
        if not active and not c['force_transcode_during_playback']:
            if r['playback_error']:
                reasons.append(r['playback_error'])
            elif norm(path) in r['playback'] or (c['pause_during_playback'] and r['playback']):
                reasons.append('Waiting for Jellyfin playback to finish before starting')
        return reasons

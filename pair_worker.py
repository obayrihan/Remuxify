"""One-time local plugin pairing. Never prints or returns the service credential."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path

from plugin_bridge import NativeJellyfin, local_url, read_secret


@contextmanager
def stopped_worker(config_path):
    path = config_path.parent / 'data/instance.lock'
    path.parent.mkdir(exist_ok=True)
    handle = path.open('a+b')
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('Stop the worker cleanly before changing its integration mode') from None
    try:
        yield
    finally:
        if handle is not None:
            handle.close()


def pair(config_path, secret_file, jellyfin_url):
    config_path = Path(config_path).resolve()
    if not config_path.is_file():
        raise ValueError('Create the local config.json from config.example.json first')
    with stopped_worker(config_path):
        settings = json.loads(config_path.read_text(encoding='utf-8-sig'))
        settings.update(integration_mode='plugin', bridge_token_file=str(Path(secret_file).resolve()),
                        jellyfin_url=local_url(jellyfin_url, allow_path=True))
        read_secret(settings)
        host = settings['host']
        local_url('http://' + ('[' + host + ']' if ':' in host else host) + ':' + str(settings['port']))
        config = type('Settings', (), {'get': lambda _: settings})()
        context = NativeJellyfin(config).context()  # Verify authorization before writing.
        pending = config_path.with_suffix('.pairing.tmp')
        try:
            with pending.open('x', encoding='utf-8') as output:
                json.dump(settings, output, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(pending, config_path)
        finally:
            if pending.exists():
                pending.unlink()
        return {'paired': True, 'protocol_version': 1, 'library_count': len(context['roots'])}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Pair a stopped Remuxify worker with its local Jellyfin plugin')
    parser.add_argument('--config', default=str(Path(__file__).parent / 'config.json'))
    parser.add_argument('--secret-file', required=True, help='Local path shown by Prepare local pairing file')
    parser.add_argument('--jellyfin-url', required=True, help='Loopback Jellyfin URL, including a configured base path if needed')
    args = parser.parse_args()
    try:
        print(json.dumps(pair(args.config, args.secret_file, args.jellyfin_url)))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, 'Pairing failed: ' + str(error) + '\n')

"""Stage/review or install the Jellyfin hotlink and Cloudflare path route.

No credentials are created. Existing settings and other menu/ingress entries are preserved.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlparse

from core import BASE, Config

STAGING = BASE / 'staging'


def configured_paths(require_tunnel=False):
    settings = Config().get()
    web = Path(settings.get('jellyfin_web', ''))
    tunnel = Path(settings.get('cloudflare_config', ''))
    if not web.is_absolute():
        raise RuntimeError('Set jellyfin_web to the absolute Jellyfin web directory')
    if require_tunnel and not tunnel.is_absolute():
        raise RuntimeError('Set cloudflare_config to an absolute path for optional tunnel integration')
    return web, tunnel


def plan():
    WEB, TUNNEL = configured_paths(require_tunnel=True)
    STAGING.mkdir(exist_ok=True)
    settings = Config().get()
    web_config = json.loads((WEB / 'config.json').read_text(encoding='utf-8-sig'))
    links = web_config.setdefault('menuLinks', [])
    links[:] = [link for link in links if link.get('name') not in ('Media Forge', 'Remuxify')]
    links.append({'name': 'Remuxify', 'icon': 'video_settings', 'url': '/web/forge.html'})
    staged_web = STAGING / 'jellyfin-web-config.json'
    staged_web.write_text(json.dumps(web_config, indent=2) + '\n', encoding='utf-8')
    tunnel = TUNNEL.read_text(encoding='utf-8-sig')
    hostname = urlparse(settings['public_url']).hostname
    if not hostname:
        raise RuntimeError('Configure a valid public_url before staging a tunnel route')
    route = (f'  - hostname: {hostname}\n'
             '    path: ^/forge(/.*)?$\n'
             f"    service: http://127.0.0.1:{int(settings['port'])}\n")
    if route not in tunnel:
        anchor = re.search(r'(?m)^  - hostname: ' + re.escape(hostname) + r'\s*$', tunnel)
        if not anchor:
            raise RuntimeError('Expected Jellyfin ingress not found; refusing to rewrite tunnel')
        tunnel = tunnel[:anchor.start()] + route + tunnel[anchor.start():]
    (STAGING / 'cloudflared-config.yml').write_text(tunnel, encoding='utf-8')
    manifest = {'files': [
        {'source': str(staged_web), 'target': str(WEB / 'config.json')},
        {'source': str(BASE / 'jellyfin-launcher.html'), 'target': str(WEB / 'forge.html')},
        {'source': str(STAGING / 'cloudflared-config.yml'), 'target': str(TUNNEL)}],
        'public_url': settings['public_url'],
        'note': 'The menu link is visible in the web client; access is enforced for administrators by the server.'}
    (STAGING / 'plan.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Prepared Jellyfin menu link, SSO launcher, and /forge/ tunnel route in ' + str(STAGING))
    return manifest


def replace_with_backup(source, target):
    source, target = Path(source), Path(target)
    if target.exists() and source.read_bytes() == target.read_bytes():
        return
    backup = BASE / 'backups' / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copy2(target, backup / target.name)
        (backup / 'restore.json').write_text(json.dumps({'backup': str(backup / target.name), 'target': str(target)}, indent=2), encoding='utf-8')
    temp = target.with_name(target.name + '.forge-install.tmp')
    try:
        with temp.open('wb') as stream:
            stream.write(source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()


def ensure_menu():
    WEB, _ = configured_paths()
    # Also used at boot to restore the link if a Jellyfin update replaced its web files.
    web_config = json.loads((WEB / 'config.json').read_text(encoding='utf-8-sig'))
    desired = {'name': 'Remuxify', 'icon': 'video_settings', 'url': '/web/forge.html'}
    links = web_config.setdefault('menuLinks', [])
    if desired not in links:
        links[:] = [link for link in links if link.get('name') not in ('Media Forge', 'Remuxify')]
        links.append(desired)
        STAGING.mkdir(exist_ok=True)
        output = STAGING / 'jellyfin-web-config.json'
        output.write_text(json.dumps(web_config, indent=2) + '\n', encoding='utf-8')
        replace_with_backup(output, WEB / 'config.json')
    replace_with_backup(BASE / 'jellyfin-launcher.html', WEB / 'forge.html')
    print('Jellyfin Remuxify link is installed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--ensure-menu', action='store_true')
    args = parser.parse_args()
    if args.ensure_menu:
        ensure_menu()
    else:
        manifest = plan()
        if args.apply:
            for item in manifest['files']:
                replace_with_backup(item['source'], item['target'])
            print('Jellyfin and tunnel configuration installed; restart cloudflared to load the route.')

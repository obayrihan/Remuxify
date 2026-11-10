"""Narrow, reversible navigation hook for Jellyfin's native plugin route."""
import re

NATIVE_LINK = {'name': 'Remuxify', 'icon': 'video_settings', 'url': '#/configurationpage?name=Remuxify'}
LEGACY_LINK = {'name': 'Media Forge', 'icon': 'video_settings', 'url': '/web/forge.html'}
SCRIPT_TAG = '<script id="remuxify-native-navigation" defer src="remuxify-navigation.js"></script>'


def set_menu(config, native):
    desired = NATIVE_LINK if native else LEGACY_LINK
    links = config.setdefault('menuLinks', [])
    # Replace the existing shortcut in place and preserve unrelated links/order.
    positions = [i for i, link in enumerate(links) if link.get('name') in ('Media Forge', 'Remuxify')]
    position = positions[0] if positions else len(links)
    links[:] = [link for link in links if link.get('name') not in ('Media Forge', 'Remuxify')]
    links.insert(min(position, len(links)), dict(desired))


def native_index(html):
    if SCRIPT_TAG in html:
        return html
    if len(re.findall(r'</head\s*>', html, re.I)) != 1:
        raise RuntimeError('Unsupported Jellyfin index.html; native navigation was not installed')
    return re.sub(r'</head\s*>', lambda match: SCRIPT_TAG + '\n' + match[0], html, count=1, flags=re.I)

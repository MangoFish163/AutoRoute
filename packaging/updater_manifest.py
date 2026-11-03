"""Generate a channel manifest after a signed Windows installer is complete."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def manifest(repository, tag, version, signature, notes, published_at=None):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid GitHub repository')
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Expected a numeric application version')
    if not re.fullmatch('v' + re.escape(version) + r'(?:-(?:unsigned|alpha|beta|rc)(?:[.-][0-9A-Za-z]+)*)?', tag):
        raise ValueError('Tag must match the application version')
    decoded = base64.b64decode(signature.strip(), validate=True).decode('utf-8')
    if len(decoded.splitlines()) != 4 or not decoded.startswith('untrusted comment:'):
        raise ValueError('Expected a Tauri updater signature')
    return {
        'version': version,
        'notes': notes,
        'pub_date': published_at or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'platforms': {'windows-x86_64': {
            'signature': signature.strip(),
            # Versioned assets are immutable; never point at latest/download.
            'url': f'https://github.com/{repository}/releases/download/{tag}/win-x64-setup.exe',
        }},
    }


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--channel', choices=['stable', 'preview'], default='preview')
    parser.add_argument('--notes-file', type=Path)
    args = parser.parse_args()
    version = json.loads((ROOT / 'frontend/package.json').read_text(encoding='utf-8'))['version']
    release = ROOT / 'release'
    installer = release / 'win-x64-setup.exe'
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    if (release / 'SHA256SUMS.txt').read_text().strip() != f'{digest}  {installer.name}':
        raise ValueError('Installer checksum does not match this build')
    signature = (release / 'win-x64-setup.exe.sig').read_text(encoding='utf-8')
    notes = args.notes_file.read_text(encoding='utf-8') if args.notes_file else f'Local Model Router v{version}'
    result = manifest(args.repository, args.tag, version, signature, notes)
    (release / f'{args.channel}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

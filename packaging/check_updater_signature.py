"""Verify the real release signature, and reject damaged package/signature bytes.

The desktop verification entry point reads files and exits before creating a
window or backend; this check never launches the installer.
"""
from pathlib import Path
import subprocess
import sys
import tempfile


def check(desktop, installer, signature):
    desktop, installer, signature = map(lambda p: Path(p).resolve(), (desktop, installer, signature))

    def verify(package, sig):
        result = subprocess.run([str(desktop), '--verify-update', str(package), str(sig)],
                                timeout=30, capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode not in (0, 1):
            raise RuntimeError(f'Desktop verification entry point failed: {result.returncode}')
        return result.returncode == 0

    if not verify(installer, signature):
        raise RuntimeError('Release signature does not match the installer and embedded public key')
    with tempfile.TemporaryDirectory(prefix='updater-validation-') as temporary:
        root = Path(temporary)
        damaged = root / 'damaged-package.exe'
        content = bytearray(installer.read_bytes())
        content[-1] ^= 1
        damaged.write_bytes(content)
        if verify(damaged, signature):
            raise RuntimeError('Verifier accepted a damaged installer')
        bad_signature = root / 'invalid.sig'
        bad_signature.write_text('invalid updater signature', encoding='utf-8')
        if verify(installer, bad_signature):
            raise RuntimeError('Verifier accepted a malformed signature')
    print('Updater signature checks passed: valid package accepted; tampered package and invalid signature rejected.')


if __name__ == '__main__':
    check(*sys.argv[1:])

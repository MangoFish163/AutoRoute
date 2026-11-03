"""Local install/repair/uninstall smoke. Refuses to replace an existing installation.

Does not launch the desktop, so an active development gateway is left alone.
All backend data and maintenance paths are isolated from the real user profile.
"""
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time
import winreg

from smoke_backend import smoke

KEY = (r'Software\Microsoft\Windows\CurrentVersion\Uninstall' + '\\'
       + os.environ.get('LOCAL_MODEL_ROUTER_TEST_PRODUCT', 'Local Model Router'))


def installed():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            return True
    except FileNotFoundError:
        return False


def run_installer(executable, signer_thumbprint=None):
    if installed():
        raise RuntimeError('Existing user installation found; refusing to replace it during smoke test')
    # Preserve a failed install and its registration for investigation. Deleting
    # its directory automatically would hide failure and leave a broken entry.
    temporary = tempfile.mkdtemp(prefix='router-installer-')
    try:
        root = Path(temporary)
        target = root / '中文 路径' / 'LocalModelRouter'
        profile = root / 'profile'
        local = profile / 'AppData' / 'Local'
        local.mkdir(parents=True)
        env = dict(os.environ, LOCALAPPDATA=str(local), USERPROFILE=str(profile))
        env.pop('LOCAL_MODEL_ROUTER_DESKTOP_INSTANCE', None)

        def execute(args):
            # NSIS /D= is intentionally last, as required by its command-line parser.
            command = (subprocess.list2cmdline(args[:-1]) + ' ' + args[-1]
                       if args[-1].startswith(('/D=', '_?=')) else args)
            result = subprocess.run(command, env=env, timeout=180,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError(f'Installer maintenance failed: {result.returncode}')

        installer = str(Path(executable).resolve())
        try:
            execute([installer, '/S', '/NS', f'/D={target}'])
            assert installed(), 'Uninstall registration not created'
            backend = target / 'backend' / 'router-backend.exe'
            assert backend.exists(), 'Backend resources missing'
            if signer_thumbprint:
                verify_env = dict(env, LMR_VERIFY_FILE=str(target / 'uninstall.exe'),
                                  LMR_VERIFY_THUMBPRINT=signer_thumbprint)
                verify = "$s=Get-AuthenticodeSignature -LiteralPath $env:LMR_VERIFY_FILE; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Thumbprint -ne $env:LMR_VERIFY_THUMBPRINT -or -not $s.TimeStamperCertificate){exit 1}"
                subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', verify],
                               env=verify_env, check=True, timeout=30,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            smoke(backend)
            marker = target / 'user-created-file.txt'
            marker.write_text('Uninstaller must preserve unknown files')
            execute([installer, '/S', '/NS', f'/D={target}'])
            assert marker.exists(), 'Repair deleted an unrelated file'
            assert backend.exists(), 'Repair did not install backend'
            # Normal launch MUST copy to TEMP and start the real uninstaller,
            # exactly like Windows Settings. _?= bypasses that stage and hid
            # the unsigned temporary executable failure on policy-enabled PCs.
            execute([str(target / 'uninstall.exe'), '/S'])
            deadline = time.monotonic() + 30
            while installed() and time.monotonic() < deadline:
                time.sleep(.2)
            assert not installed(), f'Normal uninstall failed; registration remains. Preserved: {root}'
            assert not backend.exists(), 'Tracked backend file remains'
            assert not (target / 'local-model-router.exe').exists(), 'Main executable remains'
            assert marker.exists(), 'Uninstall deleted an unrelated file'
            # Exercise actual frozen cleanup against an explicit disposable profile.
            data = profile / '.local-model-router'
            data.mkdir()
            (data / 'client.key').write_text('test-only')
            source_backend = Path('build/dist/backend/router-backend.exe').resolve()
            execute([str(source_backend), '--cleanup', '--remove-data'])
            assert not data.exists(), 'Explicit data cleanup failed'
            print('Installer smoke passed: Chinese/spaced path, installed backend, repair, '
                  'uninstall registration, unknown-file preservation, isolated data cleanup.')
        except BaseException as error:
            error.add_note(f'Installer failure artifacts preserved at {root}; no alternate uninstall launch was attempted.')
            raise
    finally:
        if not installed():
            shutil.rmtree(temporary)


if __name__ == '__main__':
    run_installer(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)

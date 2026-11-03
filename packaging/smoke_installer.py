"""Local install/repair/uninstall smoke. Refuses to replace an existing installation.

Does not launch the desktop, so an active development gateway is left alone.
All backend data and maintenance paths are isolated from the real user profile.
"""
import os
from pathlib import Path
import subprocess
import shutil
import stat
import sys
import tempfile
import time
import winreg

from smoke_backend import smoke

KEY = (r'Software\Microsoft\Windows\CurrentVersion\Uninstall' + '\\'
       + os.environ.get('LOCAL_MODEL_ROUTER_TEST_PRODUCT', 'Local Model Router'))


def cleanup_disposable_directory(temporary):
    root = Path(temporary).absolute()
    if root.parent != Path(tempfile.gettempdir()).absolute() or not root.name.startswith('router-installer-'):
        raise ValueError(f'Refusing unexpected test cleanup root: {root}')

    def remove_readonly(function, path, error):
        candidate = Path(path).absolute()
        if not candidate.is_relative_to(root):
            raise error
        # Shell-created Start Menu folders may carry the read-only attribute.
        # Change only that attribute inside our disposable tree, never ACLs or links.
        info = candidate.lstat()
        attrs = getattr(info, 'st_file_attributes', 0)
        if not isinstance(error, PermissionError) or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise error
        if not attrs & stat.FILE_ATTRIBUTE_READONLY:
            raise error
        os.chmod(candidate, stat.S_IWRITE)
        function(path)

    for attempt in range(12):
        try:
            shutil.rmtree(root, onexc=remove_readonly)
            return
        except FileNotFoundError:
            if not root.exists():
                return
            raise
        except PermissionError:
            # Normal NSIS uninstall returns from its initial process before the
            # TEMP child exits. Give that child time to release its directory.
            if attempt == 11:
                raise
            time.sleep(.5)


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
            # Exercise the updater's passive /UPDATE path without /R so this
            # isolated smoke never starts a desktop or touches a live gateway.
            data = profile / '.local-model-router'
            data.mkdir(exist_ok=True)
            protected = {
                'client.key': b'test-local-key',
                'network.json': b'{"port": 19999}',
                'router.db': b'test-database-preservation-marker',
            }
            for name, content in protected.items():
                (data / name).write_bytes(content)
            execute([installer, '/P', '/UPDATE', '/NS'])
            assert installed(), 'Update lost uninstall registration'
            assert backend.exists(), 'Update failed to restore backend at the custom install path'
            assert marker.exists(), 'Update deleted an unrelated file'
            for name, content in protected.items():
                assert (data / name).read_bytes() == content, f'Update changed user data: {name}'
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
            data.mkdir(exist_ok=True)
            (data / 'client.key').write_text('test-only')
            source_backend = Path('build/dist/backend/router-backend.exe').resolve()
            execute([str(source_backend), '--cleanup', '--remove-data'])
            assert not data.exists(), 'Explicit data cleanup failed'
            print('Installer smoke passed: Chinese/spaced path, installed backend, repair, passive update, data preservation, '
                  'uninstall registration, unknown-file preservation, isolated data cleanup.')
        except BaseException as error:
            error.add_note(f'Installer failure artifacts preserved at {root}; no alternate uninstall launch was attempted.')
            raise
    finally:
        if not installed():
            cleanup_disposable_directory(temporary)


if __name__ == '__main__':
    run_installer(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)

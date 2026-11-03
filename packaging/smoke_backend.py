"""Exercise the frozen backend with no Python/Node directories on PATH."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def smoke(executable):
    with tempfile.TemporaryDirectory(prefix='router-smoke-') as temporary:
        home = Path(temporary)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        env = dict(os.environ, LOCAL_MODEL_ROUTER_HOME=str(home / 'data'),
                   LOCAL_MODEL_ROUTER_DESKTOP_INSTANCE='packaging-smoke',
                   PATH=os.path.join(os.environ['SystemRoot'], 'System32'))
        with (home / 'backend.log').open('w+', encoding='utf-8') as log:
            process = subprocess.Popen([str(Path(executable).resolve()), '--port', str(port)],
                                       cwd=temporary, env=env, stdin=subprocess.PIPE,
                                       stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                for _ in range(150):
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
                            health = json.load(response)
                        break
                    except OSError:
                        time.sleep(.2)
                else:
                    raise RuntimeError('Frozen backend startup timeout')
                assert health['instance'] == 'packaging-smoke', health
                assert health['application'] == 'com.localmodelrouter.desktop', health
                for endpoint in ['/api/bootstrap', '/api/settings', '/api/deployments']:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}{endpoint}', timeout=5) as response:
                        assert response.status == 200
                key_path = home / 'data' / 'client.key'
                assert key_path.exists()
                process.communicate(b'stop\n', timeout=15)
                assert process.returncode == 0, process.returncode
                assert (home / 'data' / 'router.db').exists()
                print('Frozen backend smoke passed (isolated PATH, API, SQLite, graceful exit).')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    smoke(sys.argv[1])

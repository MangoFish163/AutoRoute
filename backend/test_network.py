import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest.mock import patch

from network import bind_listener, configured_port, save_port, validate_port, recommended_ports, RECOMMENDED_PORT_POOL


class NetworkTests(unittest.TestCase):
    def test_recommendations_probe_ports_and_handle_shortage_without_saving(self):
        listeners = []
        try:
            for _ in range(5):
                listener = socket.socket()
                listener.bind(('127.0.0.1', 0))
                listener.listen()
                listeners.append(listener)
            pool = tuple(listener.getsockname()[1] for listener in listeners)
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, LOCAL_MODEL_ROUTER_HOME=directory), patch('network.RECOMMENDED_PORT_POOL', pool):
                self.assertEqual(recommended_ports(), [])
                listeners[0].close()
                listeners[1].close()
                self.assertEqual(set(recommended_ports()), set(pool[:2]))
                listeners[2].close()
                self.assertEqual(set(recommended_ports()), set(pool[:3]))
                self.assertFalse(Path(directory, 'network.json').exists())
                # Probes release their sockets; a recommendation can still be bound.
                for port in pool[:3]:
                    with bind_listener(port, fallback=False):
                        pass
        finally:
            for listener in listeners:
                listener.close()

    def test_validation_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, LOCAL_MODEL_ROUTER_HOME=directory):
            self.assertEqual(configured_port(), 8787)
            for invalid in [True, '1234', 0, 1023, 65536]:
                with self.assertRaises(ValueError):
                    validate_port(invalid)
            save_port(19321)
            self.assertEqual(configured_port(), 19321)
            Path(directory, 'network.json').write_text('{broken')
            with self.assertRaises(ValueError):
                configured_port()

    def test_conflict_fallback_keeps_socket_bound(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            preferred = occupied.getsockname()[1]
            with self.assertRaises(OSError):
                bind_listener(preferred, fallback=False)
            with bind_listener(preferred) as listener:
                self.assertNotEqual(listener.getsockname()[1], preferred)
                with self.assertRaises(OSError):
                    bind_listener(listener.getsockname()[1], fallback=False)

    def test_real_launch_fallback_auth_settings_and_restart(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            original = occupied.getsockname()[1]
            Path(directory, 'network.json').write_text(json.dumps({'port': original}))
            env = {**os.environ, 'LOCAL_MODEL_ROUTER_HOME': directory, 'LOCAL_MODEL_ROUTER_DESKTOP_INSTANCE': 'network-test'}
            for launch in range(2):
                ready = Path(directory, f'ready-{launch}.json')
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('desktop_entry.py')), '--port-file', str(ready)], env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
                try:
                    token = 'a' * 64
                    process.stdin.write(token + '\n')
                    process.stdin.flush()
                    deadline = time.monotonic() + 20
                    while True:
                        try:
                            port = json.loads(ready.read_text())['port']
                            base = f'http://127.0.0.1:{port}'
                            urllib.request.urlopen(base + '/health', timeout=1).close()
                            break
                        except (OSError, ValueError):
                            if time.monotonic() > deadline or process.poll() is not None:
                                self.fail('backend did not become ready')
                            time.sleep(.1)
                    def request(path, method='GET', data=None):
                        req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data else None, method=method, headers={'X-Management-Token': token, 'Content-Type': 'application/json'})
                        with urllib.request.urlopen(req, timeout=3) as response:
                            return json.load(response)
                    info = request('/api/bootstrap')
                    self.assertEqual(info['base_url'], base + '/v1')
                    self.assertEqual(info['port_changed_from'], original if launch == 0 else None)
                    if launch == 0:
                        selected = port
                        self.assertNotEqual(port, original)
                    else:
                        self.assertEqual(port, selected)
                    self.assertEqual(request('/api/network')['port'], port)
                    recommendations = request('/api/network/recommendations')['ports']
                    self.assertLessEqual(len(recommendations), 3)
                    self.assertEqual(len(recommendations), len(set(recommendations)))
                    self.assertTrue(set(recommendations).issubset(RECOMMENDED_PORT_POOL))
                    self.assertEqual(request('/api/network')['port'], port)
                    request('/api/network', 'PUT', {'port': port})
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        request('/api/network', 'PUT', {'port': 65536})
                    self.assertEqual(error.exception.code, 422)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(base + '/api/network')
                    self.assertEqual(error.exception.code, 401)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(base + '/api/network/recommendations')
                    self.assertEqual(error.exception.code, 401)
                finally:
                    process.stdin.write('stop\n')
                    process.stdin.flush()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    process.stdin.close()


if __name__ == '__main__':
    unittest.main()

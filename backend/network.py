"""Persistent loopback listener configuration shared by all launchers."""

import errno
import json
import os
import random
from pathlib import Path
import socket
import tempfile

DEFAULT_PORT = 8787
# Product suggestions, not reserved ports. Always check the local machine first.
RECOMMENDED_PORT_POOL = (8787, 9999, 10010, 18787, 28787, 38787, 48787, 19999, 29999, 39999)


def config_path():
    return (
        Path(os.environ.get("LOCAL_MODEL_ROUTER_HOME", Path.home() / ".local-model-router"))
        / "network.json"
    )


def validate_port(port):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("端口必须是 1024～65535 的整数")
    return port


def configured_port():
    path = config_path()
    if not path.exists():
        return DEFAULT_PORT
    return validate_port(json.loads(path.read_text(encoding="utf-8"))["port"])


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_port(port):
    write_json(config_path(), {"port": validate_port(port)})


def recommended_ports():
    ports = []
    for port in random.sample(RECOMMENDED_PORT_POOL, len(RECOMMENDED_PORT_POOL)):
        try:
            with bind_listener(port, fallback=False):
                ports.append(port)
        except OSError as error:
            if error.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
        if len(ports) == 3:
            break
    return ports


def bind_listener(preferred, fallback=True):
    """Keep the socket bound through server startup; no probe/close race."""
    validate_port(preferred)
    candidates = [preferred]
    if fallback:
        candidates += list(range(preferred + 1, min(preferred + 100, 65535) + 1)) + [0]
    for port in candidates:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
            listener.setblocking(False)
            return listener
        except OSError as error:
            listener.close()
            if error.errno not in (errno.EADDRINUSE, errno.EACCES) or port == candidates[-1]:
                raise
    raise OSError("没有可用的本地端口")

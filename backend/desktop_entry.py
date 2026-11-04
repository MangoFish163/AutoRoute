"""Frozen desktop backend and narrowly scoped installer maintenance commands."""

from __future__ import annotations

import argparse
import multiprocessing
import os
from pathlib import Path
import shutil
import stat
import sys
import threading


def checked_tree(path: Path) -> None:
    """Preflight the whole tree before any deletion; never follow reparse points."""
    for parent in [path, *path.parents]:
        if os.path.lexists(parent):
            attrs = parent.lstat().st_file_attributes if os.name == "nt" else 0
            if parent.is_symlink() or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f"Refusing reparse point: {parent}")
    if path.exists() and path.is_dir():
        for root, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                entry = Path(root) / name
                attrs = entry.lstat().st_file_attributes if os.name == "nt" else 0
                if entry.is_symlink() or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError(f"Refusing reparse point: {entry}")


def cleanup(remove_data: bool) -> None:
    # Never consume LOCAL_MODEL_ROUTER_HOME here: it may point at unrelated files.
    local = Path(os.environ["LOCALAPPDATA"])
    targets = [local / "LocalModelRouter", local / "com.localmodelrouter.desktop"]
    if remove_data:
        targets.append(Path.home() / ".local-model-router")
    for target in targets:
        checked_tree(target)
    for target in targets:
        if target.exists():
            shutil.rmtree(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--remove-data", action="store_true")
    parser.add_argument("--validate-tree", type=Path)
    parser.add_argument("--port", type=int, help="Explicit port (no automatic fallback)")
    parser.add_argument("--port-file", type=Path)
    args = parser.parse_args()
    try:
        if args.validate_tree is not None:
            checked_tree(args.validate_tree)
            return 0
        if args.cleanup:
            cleanup(args.remove_data)
            return 0
        import uvicorn
        from app import app

        if os.environ.get("LOCAL_MODEL_ROUTER_DESKTOP_INSTANCE"):
            # First line of the inherited private pipe is the per-launch management
            # token; subsequent input/EOF remains the shutdown signal.
            token = sys.stdin.readline(256).strip() if sys.stdin is not None else ""
            if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
                raise ValueError("Missing or invalid desktop management token")
            app.state.management_token = token
        from network import configured_port, bind_listener, save_port, write_json

        preferred = args.port if args.port is not None else configured_port()
        listener = bind_listener(preferred, fallback=args.port is None)
        port = listener.getsockname()[1]
        try:
            if args.port is None:
                save_port(port)
            app.state.listener_port = port
            app.state.port_changed_from = preferred if preferred != port else None
            if args.port_file:
                write_json(args.port_file, {"port": port})
        except Exception:
            listener.close()
            raise
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                workers=1,
                loop="asyncio",
                http="h11",
                ws="none",
                timeout_graceful_shutdown=8,
            )
        )

        def watch_parent():
            # A private inherited stdin pipe is the desktop's shutdown channel.
            # EOF also stops the service if the parent dies before Job assignment.
            if sys.stdin is not None:
                sys.stdin.readline()
                server.should_exit = True

        if os.environ.get("LOCAL_MODEL_ROUTER_DESKTOP_INSTANCE"):
            threading.Thread(target=watch_parent, daemon=True).start()
        try:
            server.run(sockets=[listener])
        finally:
            listener.close()
        return 0 if server.started else 1
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())

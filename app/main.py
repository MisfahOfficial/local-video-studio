from __future__ import annotations

import argparse
import threading
import webbrowser

from .paths import AppPaths
from .server import create_server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Local Video Studio")
    parser.add_argument("--host", default="127.0.0.1", help="Local address to bind")
    parser.add_argument("--port", type=int, default=8765, help="Local port")
    parser.add_argument("--data-dir", help="Override the project data directory")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the interface automatically")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = AppPaths.resolve(args.data_dir)
    server = create_server(paths, args.host, args.port)
    url = f"http://{args.host}:{args.port}"
    print(f"Local Video Studio is running at {url}")
    print(f"Project data: {paths.root}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Local Video Studio…")
    finally:
        server.server_close()


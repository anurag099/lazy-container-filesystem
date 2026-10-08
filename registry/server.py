#!/usr/bin/env python3
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    root = Path('.')

    def do_GET(self):
        prefix = "/blob/"
        if not self.path.startswith(prefix):
            self.send_error(404)
            return
        digest = self.path[len(prefix):].split("?", 1)[0]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            self.send_error(400)
            return
        path = self.root / digest
        if not path.is_file():
            self.send_error(404)
            return
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        with path.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                self.wfile.write(chunk)

    def log_message(self, fmt, *args):
        print(fmt % args)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--port", type=int, default=9000)
    args = p.parse_args()
    Handler.root = Path(args.root).resolve()
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()

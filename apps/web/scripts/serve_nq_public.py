#!/usr/bin/env python3
"""Serve Futures Research production dist + proxy /api /health → :8000."""
from __future__ import annotations

import http.client
import os
import socketserver
import sys
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

DIST = Path(
    os.environ.get(
        "ALOG_DIST",
        str(Path.home() / "Library/Application Support/alog-trading/public-web/dist"),
    )
).resolve()
HOST = os.environ.get("ALOG_PUBLIC_HOST", "127.0.0.1")
PORT = int(os.environ.get("ALOG_PUBLIC_PORT", "4173"))
API_HOST = os.environ.get("ALOG_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("ALOG_API_PORT", "8000"))

# Always revalidate — clients poll these for optimistic auto-update.
NO_CACHE_PATHS = {
    "/",
    "/index.html",
    "/sw.js",
    "/build-meta.json",
    "/manifest.webmanifest",
}


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        if self.path.startswith("/api") or self.path.startswith("/health"):
            return self._proxy()
        return super().do_GET()

    def do_HEAD(self):
        if self.path.startswith("/api") or self.path.startswith("/health"):
            return self._proxy()
        return super().do_HEAD()

    def do_POST(self):
        if self.path.startswith("/api"):
            return self._proxy()
        self.send_error(405)

    def do_PUT(self):
        if self.path.startswith("/api"):
            return self._proxy()
        self.send_error(405)

    def do_PATCH(self):
        if self.path.startswith("/api"):
            return self._proxy()
        self.send_error(405)

    def do_DELETE(self):
        if self.path.startswith("/api"):
            return self._proxy()
        self.send_error(405)

    def do_OPTIONS(self):
        if self.path.startswith("/api") or self.path.startswith("/health"):
            return self._proxy()
        self.send_response(204)
        self.end_headers()

    def end_headers(self):
        path = urlsplit(self.path).path
        if (
            path in NO_CACHE_PATHS
            or path.endswith(".html")
            or path == ""
            or path.endswith("/sw.js")
            or path.endswith("build-meta.json")
        ):
            # Origin + Cloudflare edge: never cache update-critical files
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("CDN-Cache-Control", "no-store")
            self.send_header("Cloudflare-CDN-Cache-Control", "no-store")
        elif "/assets/" in path:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        super().end_headers()

    def _proxy(self):
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else None
        conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=120)
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in ("host", "connection")
        }
        headers["Host"] = f"{API_HOST}:{API_PORT}"
        headers["Connection"] = "close"
        try:
            conn.request(
                self.command,
                parsed.path + (("?" + parsed.query) if parsed.query else ""),
                body=body,
                headers=headers,
            )
            resp = conn.getresponse()
            data = resp.read()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in ("transfer-encoding", "connection", "content-encoding"):
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except Exception as exc:
            self.send_error(502, f"upstream: {exc}")
        finally:
            conn.close()

    def translate_path(self, path):
        # SPA fallback: unknown paths → index.html
        full = super().translate_path(path)
        rel = Path(full)
        if rel.is_file():
            return str(rel)
        if path.startswith("/api") or path.startswith("/health"):
            return full
        index = DIST / "index.html"
        if index.is_file():
            return str(index)
        return full


def main():
    if not (DIST / "index.html").is_file():
        print(f"dist missing: {DIST}", file=sys.stderr)
        sys.exit(1)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((HOST, PORT), Handler) as httpd:
        print(
            f"serving {DIST} on http://{HOST}:{PORT} api→{API_HOST}:{API_PORT}",
            flush=True,
        )
        httpd.serve_forever()


if __name__ == "__main__":
    main()

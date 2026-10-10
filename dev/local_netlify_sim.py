#!/usr/bin/env python3
"""Local simulator of the Netlify setup (static site + /api proxy).

Deploy se PEHLE poori website locally test karne ke liye:
  terminal 1:  cd backend && gunicorn -w 2 -b 127.0.0.1:8081 --timeout 300 wsgi:app
  terminal 2:  python dev/local_netlify_sim.py
  browser:     http://localhost:8080

/api/* requests backend (8081) par forward hote hain — bilkul waise hi
jaise Netlify ka proxy production me karta hai. (Sirf testing ke liye;
production me Netlify khud proxy karta hai.)
"""
import http.server
import socketserver
import urllib.request
import urllib.error
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
BACKEND = os.environ.get("BACKEND", "http://127.0.0.1:8081")
PORT = int(os.environ.get("PORT", "8080"))

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".css": "text/css",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".xml": "application/xml",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json",
    ".woff2": "font/woff2",
}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        url = BACKEND + self.path
        req = urllib.request.Request(url, data=body, method=self.command)
        for h in ("Content-Type", "X-API-Key", "Cookie"):
            if self.headers.get(h):
                req.add_header(h, self.headers[h])
        try:
            resp = urllib.request.urlopen(req, timeout=300)
            data, status, hdrs = resp.read(), resp.status, resp.headers
        except urllib.error.HTTPError as e:
            data, status, hdrs = e.read(), e.code, e.headers
        except Exception as e:
            msg = f'{{"error":"Backend not reachable on {BACKEND} — gunicorn chalu karein: {e}"}}'.encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(msg)
            return
        self.send_response(status)
        for k, v in hdrs.items():
            if k.lower() not in ("transfer-encoding", "connection", "content-encoding"):
                self.send_header(k, v)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/":
            path = "/index.html"
        fpath = os.path.normpath(os.path.join(SITE, path.lstrip("/")))
        if not fpath.startswith(SITE) or not os.path.isfile(fpath):
            self.send_response(404)
            msg = b"Not found"
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        ext = os.path.splitext(fpath)[1].lower()
        with open(fpath, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._proxy() if self.path.startswith("/api/") else self._serve_file()

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        self._proxy()

    def log_message(self, fmt, *args):
        sys.stderr.write("  [sim] %s\n" % (fmt % args))


if __name__ == "__main__":
    print(f"Netlify simulator: http://localhost:{PORT}  (static: site/, api -> {BACKEND})")
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        httpd.serve_forever()

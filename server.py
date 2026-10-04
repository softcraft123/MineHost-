#!/usr/bin/env python3
"""
EmberForge site server.

Serves the marketing site plus real, working endpoints:

  GET  /                     landing page
  GET  /download/<name>      download a build (with byte-accurate Content-Length)
  GET  /api/builds           JSON list of builds with size + SHA-256
  GET  /api/compare          JSON feature comparison against other servers
  GET  /api/health           JSON health/telemetry
  POST /api/upload           multipart upload of a new build (size-capped,
                             hashed, stored, and listed)

Only files under downloads/ and uploads/ are ever served; there is no path
traversal out of those directories. Uploads are capped and validated.
"""

import hashlib
import json
import os
import re
import socket
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS = os.path.join(ROOT, "downloads")
UPLOADS = os.path.join(ROOT, "uploads")
INDEX = os.path.join(ROOT, "index.html")

# 64 MiB ceiling: generous for a server binary, small enough to stay safe.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
# Only these extensions may be stored as builds.
ALLOWED_EXT = {".exe", ".zip", ".tar", ".gz", ".tgz", ".bin", ".jar", ".msi"}

STARTED = time.time()

# --------------------------------------------------------------------------
# Feature matrix: our server vs other Minecraft server implementations.
# --------------------------------------------------------------------------
FEATURES = [
    # (feature, emberforge, vanilla, paper, fabric, nukkit, pocketmine)
    ("Language", "Rust", "Java", "Java", "Java", "Java", "PHP"),
    ("Memory per player", "~0.5 MB", "~30 MB", "~40 MB", "~45 MB", "~8 MB", "~10 MB"),
    ("Single-file binary", True, False, False, False, False, False),
    ("Cold start", "instant", "slow", "slow", "slow", "medium", "medium"),
    ("Clean-room (no Mojang code)", True, False, False, False, True, True),
    ("Minecraft Java 1.20.4", True, True, True, True, False, False),
    ("Bedrock edition", False, True, False, False, True, True),
    ("Plugin API", False, True, True, True, True, True),
    ("Mods supported", False, False, False, True, False, False),
    ("World persistence", True, True, True, True, True, True),
    ("Online-mode auth", True, True, True, True, False, False),
    ("Encrypted connection", True, True, True, True, False, False),
    ("Whitelist", True, True, True, True, True, True),
    ("Built-in status dashboard", True, False, False, False, False, False),
    ("Mobs / AI", True, True, True, True, True, True),
    ("Custom block drops", True, False, True, True, True, True),
]

COLUMNS = ["emberforge", "vanilla", "paper", "fabric", "nukkit", "pocketmine"]

VERDICTS = [
    {
        "name": "EmberForge",
        "tag": "this project",
        "good": "Tiny footprint, instant start, single binary, encrypted, no JVM.",
        "bad": "No plugins or mods; young codebase; not battle-tested at scale.",
        "pick": "Self-hosting on modest hardware, embedded/edge use, learning.",
    },
    {
        "name": "Vanilla (Mojang)",
        "tag": "reference",
        "good": "The ground truth. Perfect parity with the client.",
        "bad": "Heavy, slow start, no plugin API, memory hungry.",
        "pick": "You want exactly-official behaviour and nothing else.",
    },
    {
        "name": "Paper",
        "tag": "production standard",
        "good": "Huge plugin ecosystem, battle-tested, great performance tuning.",
        "bad": "JVM memory overhead, still large, needs Java runtime.",
        "pick": "Public servers with plugins and many players.",
    },
    {
        "name": "Fabric",
        "tag": "modding",
        "good": "Best modding story; huge mod ecosystem.",
        "bad": "Heavier still; mods can break between versions.",
        "pick": "Modded survival with friends.",
    },
    {
        "name": "Nukkit",
        "tag": "bedrock",
        "good": "Bedrock support; Java, plugin API.",
        "bad": "Bedrock only; no Java-edition clients.",
        "pick": "Bedrock/mobile players.",
    },
    {
        "name": "PocketMine-MP",
        "tag": "bedrock / PHP",
        "good": "Mature Bedrock server; large plugin base.",
        "bad": "PHP runtime; Bedrock only.",
        "pick": "Bedrock servers with plugins.",
    },
]


def parse_multipart(body, boundary):
    """
    Minimal multipart/form-data parser (the stdlib `cgi` module was removed in
    Python 3.13). Returns a dict of field name -> (filename or None, bytes).

    Only what this endpoint needs: a single file field, bounded in size by the
    caller before this is invoked.
    """
    delim = b"--" + boundary
    parts = body.split(delim)
    out = {}
    for part in parts:
        if not part or part in (b"--", b"--\r\n", b"\r\n"):
            continue
        part = part.lstrip(b"\r\n")
        if b"\r\n\r\n" not in part:
            continue
        raw_headers, data = part.split(b"\r\n\r\n", 1)
        # Strip the trailing CRLF that precedes the next boundary.
        if data.endswith(b"\r\n"):
            data = data[:-2]
        headers = {}
        for line in raw_headers.split(b"\r\n"):
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.strip().lower()] = v.strip()
        disp = headers.get(b"content-disposition", b"").decode("utf-8", "replace")
        m = re.search(r'name="([^"]*)"', disp)
        if not m:
            continue
        name = m.group(1)
        fn = re.search(r'filename="([^"]*)"', disp)
        out[name] = (fn.group(1) if fn else None, data)
    return out


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


def list_builds():
    builds = []
    for folder, kind in ((DOWNLOADS, "official"), (UPLOADS, "uploaded")):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in ALLOWED_EXT and ext != ".txt":
                continue
            size = os.path.getsize(path)
            builds.append(
                {
                    "name": name,
                    "kind": kind,
                    "size": size,
                    "size_human": human(size),
                    "sha256": sha256_of(path),
                    "modified": int(os.path.getmtime(path)),
                    "url": f"/download/{name}",
                    "ext": ext.lstrip("."),
                }
            )
    return builds


class Handler(BaseHTTPRequestHandler):
    server_version = "EmberForgeSite/1.0"

    def log_message(self, fmt, *args):
        print(f"[site] {self.address_string()} {fmt % args}")

    # ---------------- helpers ----------------
    def _send(self, code, ctype, body, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj, indent=2), extra)

    def _safe_path(self, base, name):
        """Resolve `name` under `base`, refusing anything that escapes it."""
        name = unquote(name)
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        path = os.path.realpath(os.path.join(base, name))
        if os.path.dirname(path) != os.path.realpath(base):
            return None
        return path if os.path.isfile(path) else None

    # ---------------- routes ----------------
    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            try:
                with open(INDEX, "rb") as fh:
                    self._send(200, "text/html; charset=utf-8", fh.read())
            except OSError:
                self._send(500, "text/plain", "index.html missing\n")
            return

        if path == "/api/builds":
            builds = list_builds()
            self._json(200, {"count": len(builds), "builds": builds})
            return

        if path == "/api/compare":
            self._json(200, {
                "columns": COLUMNS,
                "features": [{"feature": f, "values": list(v)} for f, *v in FEATURES],
                "verdicts": VERDICTS,
            })
            return

        if path == "/api/health":
            builds = list_builds()
            self._json(200, {
                "status": "ok",
                "uptime_secs": int(time.time() - STARTED),
                "hostname": socket.gethostname(),
                "builds": len(builds),
                "uploads": len([b for b in builds if b["kind"] == "uploaded"]),
                "max_upload_bytes": MAX_UPLOAD_BYTES,
            })
            return

        if path.startswith("/download/"):
            name = path[len("/download/"):]
            for base in (DOWNLOADS, UPLOADS):
                target = self._safe_path(base, name)
                if target:
                    size = os.path.getsize(target)
                    with open(target, "rb") as fh:
                        data = fh.read()
                    ctype = ("application/octet-stream"
                             if not target.endswith(".txt")
                             else "text/plain; charset=utf-8")
                    self._send(200, ctype, data, extra={
                        "Content-Disposition": f'attachment; filename="{os.path.basename(target)}"',
                        "X-File-Size": str(size),
                    })
                    return
            self._send(404, "text/plain", "no such build\n")
            return

        self._send(404, "text/plain", "not found\n")

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/upload":
            self._json(404, {"error": "not found"})
            return

        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            self._json(400, {"error": "expected multipart/form-data"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._json(400, {"error": "empty body"})
            return
        if length > MAX_UPLOAD_BYTES + 4096:
            self._json(413, {"error": f"upload exceeds {human(MAX_UPLOAD_BYTES)}"})
            return

        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if not m:
            self._json(400, {"error": "missing multipart boundary"})
            return
        body = self.rfile.read(length)
        fields = parse_multipart(body, m.group(1).encode())
        if "file" not in fields:
            self._json(400, {"error": "missing 'file' field"})
            return
        filename, data = fields["file"]
        if not filename:
            self._json(400, {"error": "no filename"})
            return

        raw_name = os.path.basename(filename)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name)[:120]
        ext = os.path.splitext(safe)[1].lower()
        if ext not in ALLOWED_EXT:
            self._json(400, {"error": f"extension {ext!r} not allowed",
                             "allowed": sorted(ALLOWED_EXT)})
            return

        if len(data) > MAX_UPLOAD_BYTES:
            self._json(413, {"error": f"upload exceeds {human(MAX_UPLOAD_BYTES)}"})
            return

        os.makedirs(UPLOADS, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        stored = f"{stamp}-{uuid.uuid4().hex[:8]}-{safe}"
        dest = os.path.join(UPLOADS, stored)
        with open(dest, "wb") as fh:
            fh.write(data)

        self._json(201, {
            "ok": True,
            "stored": stored,
            "size": len(data),
            "size_human": human(len(data)),
            "sha256": hashlib.sha256(data).hexdigest(),
            "url": f"/download/{stored}",
        })


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=12000)
    ap.add_argument("--bind", default="0.0.0.0")
    args = ap.parse_args()

    os.makedirs(DOWNLOADS, exist_ok=True)
    os.makedirs(UPLOADS, exist_ok=True)
    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"[site] serving http://{args.bind}:{args.port}/  (root={ROOT})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[site] stopped")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""InPage → PDF Converter — backend service.

A self-contained local web service that converts InPage Urdu documents
(.inp) into PDFs with original Nastaliq typography, original line breaks,
original vertical gaps, recovered font sizes, digit-order correction and
table reconstruction.

Architecture
------------
    routes (this file)
        ├── POST /api/convert    convert .inp file(s) → PDF + page images
        ├── GET  /api/view/…     serve a converted PDF inline (pdf.js uses this)
        ├── GET  /api/download/… serve a converted PDF as attachment
        ├── GET  /api/pageimg/…  rasterised page JPEGs (thumbnails / fallback)
        ├── POST /api/font       upload a custom Urdu .ttf font
        └── GET  /api/health     service status for monitoring

    core (inpage2pdf package)
        ├── inp_reader.py   OLE2 + InPage100 stream walker (+ style runs)
        ├── mapping.py      InPage byte codes → Unicode (ordered rules)
        ├── engine.py       conversion engine with unmapped-code reporting
        ├── textfix.py      conversion-artifact repairs
        ├── pdf_builder.py  Nastaliq typesetter (line-faithful, gap-aware,
        │                   table reconstruction, Jameel Noori Nastaleeq)
        └── converter.py    end-to-end orchestration

Everything runs on the machine this server runs on — no file ever
leaves it. Jobs live in a temp dir and are re-registered on restart so
old links keep working; they are swept after MAX_AGE_SECONDS.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time
import uuid

import pypdfium2 as pdfium
from flask import Flask, jsonify, render_template, request, send_file

from inpage2pdf.converter import convert_file
from inpage2pdf.pdf_builder import RenderOptions

# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(tempfile.gettempdir(), "inpage2pdf_jobs")
CONVERTED_DIR = os.path.join(APP_DIR, "converted")
FONT_DIR = os.path.join(APP_DIR, "fonts")
os.makedirs(WORK_DIR, exist_ok=True)
os.makedirs(CONVERTED_DIR, exist_ok=True)

MAX_CONTENT_LENGTH = 200 * 1024 * 1024      # 200 MB per request
MAX_FILES_PER_REQUEST = 30
MAX_AGE_SECONDS = 24 * 3600                 # job lifetime
PREVIEW_PAGES = 60                          # pages rasterised per PDF
PREVIEW_CHARS = 200_000                     # text preview cap
ALLOWED_FONTS = {"jameel", "noto", "custom"}
ALLOWED_LAYOUTS = {"auto", "table", "flow"}
ALLOWED_PAGES = {"A4", "Letter", "Legal"}

# --------------------------------------------------------------------------
# app setup
# --------------------------------------------------------------------------
app = Flask(__name__, static_folder=os.path.join(APP_DIR, "static"))
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
app.config["JSON_SORT_KEYS"] = False

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("inpage2pdf")
# font subsetting logs are noisy at INFO — keep warnings only
logging.getLogger("fpdf").setLevel(logging.WARNING)
logging.getLogger("fontTools").setLevel(logging.WARNING)

JOBS: dict[str, dict] = {}                  # job_id -> {dir, created}

# --------------------------------------------------------------------------
# security layer (round 9): rate limiting, optional API key, upload checks
# --------------------------------------------------------------------------
# API key: leave INPAGE2PDF_API_KEY unset for a fully public site (protected
# only by rate limits). Set it to lock conversion behind a key — then share
# the link  https://your-site/?key=THE_KEY  with your users; the page picks
# it up automatically and sends it with every convert request.
API_KEY = os.environ.get("INPAGE2PDF_API_KEY", "").strip()

# per-IP sliding-window rate limits: (window_seconds, max_requests)
RATE_LIMITS = {
    "/api/convert": (15 * 60, 60),          # conversion is CPU-heavy
    "/api/font":    (60 * 60, 10),          # persistent file write
    "__gets__":     (5 * 60, 900),          # view / download / pageimg
}
_rate_bins: dict[tuple, list] = {}


def _client_ip() -> str:
    """Best-effort client IP (behind the sandbox/reverse proxy)."""
    xf = request.headers.get("X-Forwarded-For", "")
    if xf:
        return xf.split(",")[0].strip()
    return request.remote_addr or "?"


def _rate_limited(bucket: str) -> int | None:
    """Return Retry-After seconds if over limit, else None."""
    window, cap = RATE_LIMITS[bucket]
    key = (bucket, _client_ip())
    now = time.time()
    hits = [t for t in _rate_bins.get(key, []) if now - t < window]
    if len(hits) >= cap:
        _rate_bins[key] = hits
        return int(window - (now - hits[0])) + 1
    hits.append(now)
    _rate_bins[key] = hits
    if len(_rate_bins) > 4096:              # crude memory guard
        _rate_bins.clear()
    return None


def _check_api_key() -> str | None:
    """If an API key is configured, require it on expensive endpoints."""
    if not API_KEY:
        return None
    supplied = (request.headers.get("X-API-Key", "")
                or request.form.get("api_key", "")
                or request.args.get("api_key", ""))
    if supplied != API_KEY:
        return "Invalid or missing API key."
    return None


def _valid_font_bytes(data: bytes) -> bool:
    """True only for real font files (sfnt containers)."""
    if len(data) < 12 or len(data) > 20 * 1024 * 1024:
        return False
    return data[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")


def _restore_jobs_from_disk() -> None:
    """Re-register job folders found on disk so view/download links keep
    working across server restarts (fixes 404s on old links)."""
    for name in os.listdir(WORK_DIR):
        full = os.path.join(WORK_DIR, name)
        if re.fullmatch(r"[0-9a-f]{12}", name) and os.path.isdir(full):
            JOBS[name] = {"dir": full, "created": os.path.getmtime(full)}


_restore_jobs_from_disk()


def _sweep_old_jobs() -> None:
    now = time.time()
    for jid in list(JOBS):
        if now - JOBS[jid]["created"] > MAX_AGE_SECONDS:
            shutil.rmtree(JOBS[jid]["dir"], ignore_errors=True)
            JOBS.pop(jid, None)
    # also sweep job dirs on disk unknown to this worker (multi-worker safe)
    try:
        for name in os.listdir(WORK_DIR):
            full = os.path.join(WORK_DIR, name)
            if (re.fullmatch(r"[0-9a-f]{12}", name) and os.path.isdir(full)
                    and now - os.path.getmtime(full) > MAX_AGE_SECONDS):
                shutil.rmtree(full, ignore_errors=True)
    except OSError:
        pass


def _job_file(job_id: str, name: str) -> str | None:
    """Safely resolve a file inside a job dir (no traversal).

    NOTE: the earlier guard rejected any name containing ".." — which
    wrongly 404'd legitimate files like "urdu paer class 10..inp"
    (double dot typed before the extension). Traversal safety is now
    enforced with basename + realpath containment instead, so such
    filenames work while real traversal attempts still fail.

    Round 9: resolves from DISK (not the in-memory JOBS dict) so the app
    also works under multi-process servers (gunicorn -w 2+) and keeps
    serving jobs created by another worker."""
    if not re.fullmatch(r"[0-9a-f]{12}", job_id or ""):
        return None
    job_dir = os.path.join(WORK_DIR, job_id)
    base = os.path.basename((name or "").replace("\\", "/"))
    if base in ("", ".", ".."):
        return None
    path = os.path.join(job_dir, base)
    if not os.path.isfile(path):
        return None
    real_root = os.path.realpath(job_dir)
    real_path = os.path.realpath(path)
    if not real_path.startswith(real_root + os.sep):
        return None
    return path


def _render_page_images(pdf_path: str, job_dir: str, stem: str):
    """Rasterise the first PREVIEW_PAGES pages of a converted PDF into
    JPEGs — used for thumbnails and as a preview fallback in environments
    where PDF rendering is unavailable."""
    try:
        pdf = pdfium.PdfDocument(pdf_path)
        total = len(pdf)
        names = []
        for i in range(min(PREVIEW_PAGES, total)):
            img = pdf[i].render(scale=1.6).to_pil().convert("RGB")
            name = f"{stem}_p{i + 1:02d}.jpg"
            img.save(os.path.join(job_dir, name), "JPEG", quality=85)
            names.append(name)
        return names, total
    except Exception as exc:                # noqa: BLE001 — preview must never kill a conversion
        log.warning("page image render failed: %s", exc)
        return [], 0


def _parse_options(form) -> RenderOptions:
    """Translate validated form fields into RenderOptions."""
    try:
        scale = float(form.get("scale", 1.0))
    except ValueError:
        scale = 1.0
    try:
        line_spacing = float(form.get("line_spacing", 1.7))
    except ValueError:
        line_spacing = 1.7
    layout = form.get("layout_mode", "auto")
    if layout not in ALLOWED_LAYOUTS:
        layout = "auto"
    font = form.get("urdu_font", "jameel")
    if font not in ALLOWED_FONTS:
        font = "jameel"
    page = form.get("page_size", "A4")
    if page not in ALLOWED_PAGES:
        page = "A4"
    return RenderOptions(
        page_size=page,
        scale=max(0.5, min(2.0, scale)),
        line_spacing=max(1.2, min(2.5, line_spacing)),
        detect_headings=form.get("detect_headings", "1") == "1",
        page_numbers=form.get("page_numbers") == "1",
        strip_kashida=form.get("strip_kashida", "1") == "1",
        table_mode=layout,
        urdu_font=font,
        exact_lines=form.get("reflow", "0") != "1",
        orientation="L" if form.get("landscape") == "1" else "P",
    )


def _api_error(message: str, status: int = 400):
    return jsonify(error=message), status


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
@app.get("/")
def index():
    # base URL for canonical / og:url / sitemap links (SEO)
    base = request.url_root.rstrip("/")
    return render_template("index.html", base_url=base)


@app.get("/sitemap.xml")
def sitemap():
    base = request.url_root.rstrip("/")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'  <url><loc>{base}/</loc><changefreq>weekly</changefreq>'
        '<priority>1.0</priority></url>\n'
        '</urlset>\n'
    )
    return app.response_class(xml, mimetype="application/xml")


@app.get("/robots.txt")
def robots():
    base = request.url_root.rstrip("/")
    txt = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return app.response_class(txt, mimetype="text/plain")


# --------------------------------------------------------------------------
# conversion API
# --------------------------------------------------------------------------
@app.post("/api/convert")
def api_convert():
    _sweep_old_jobs()

    rl = _rate_limited("/api/convert")
    if rl is not None:
        return _api_error("Bohat zyada requests — thodi der baad koshish karein.",
                          429)
    key_err = _check_api_key()
    if key_err:
        return _api_error(key_err, 401)

    files = request.files.getlist("files")
    if not files:
        return _api_error("No files received.")
    if len(files) > MAX_FILES_PER_REQUEST:
        return _api_error(f"Too many files at once (max {MAX_FILES_PER_REQUEST}).")

    opts = _parse_options(request.form)
    apply_fixes = request.form.get("apply_fixes", "1") == "1"
    latin_digits = request.form.get("latin_digits") == "1"
    title_from_filename = request.form.get("cover_title") == "1"

    job_id = uuid.uuid4().hex[:12]
    job_dir = os.path.join(WORK_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    results = []
    for f in files:
        safe_name = os.path.basename(f.filename or "document.inp")
        if not safe_name.lower().endswith(".inp"):
            results.append({"name": safe_name, "ok": False,
                            "error": "Not an .inp file."})
            continue

        src = os.path.join(job_dir, safe_name)
        f.save(src)
        out = os.path.join(job_dir, os.path.splitext(safe_name)[0] + ".pdf")
        t0 = time.time()
        res = convert_file(src, out, opts,
                           apply_fixes=apply_fixes,
                           latin_digits=latin_digits,
                           title_from_filename=title_from_filename)
        log.info("convert %s -> ok=%s (%.2fs)", safe_name, res.ok,
                 time.time() - t0)

        entry = {"name": safe_name, "ok": res.ok}
        if res.ok:
            pdf_base = os.path.basename(out)
            entry["pdf"] = pdf_base
            entry["size"] = os.path.getsize(out)
            entry["stats"] = res.stats
            entry["unmapped"] = res.unmapped[:8]
            entry["fixes"] = len(res.fixes)
            entry["preview"] = res.preview_text[:PREVIEW_CHARS]
            entry["preview_paras"] = res.preview_paras
            stem = re.sub(r"[^A-Za-z0-9_-]+", "_",
                          os.path.splitext(pdf_base)[0]).strip("_") or "doc"
            entry["page_images"], entry["total_pages"] = \
                _render_page_images(out, job_dir, stem)
            # persistent workspace copy under a clean name (overwrites
            # older conversions of the same file)
            try:
                shutil.copy(out, os.path.join(CONVERTED_DIR, pdf_base))
                entry["workspace_copy"] = pdf_base
            except OSError:
                pass
        else:
            entry["error"] = res.error
        results.append(entry)

    JOBS[job_id] = {"dir": job_dir, "created": time.time()}
    return jsonify(job=job_id, results=results)


# --------------------------------------------------------------------------
# file serving API
# --------------------------------------------------------------------------
def _get_guard():
    """Shared limiter for the (cheap but enumerable) GET endpoints."""
    rl = _rate_limited("__gets__")
    if rl is not None:
        return _api_error("Bohat zyada requests — thodi der baad koshish karein.",
                          429)
    return None


@app.get("/api/view/<job_id>/<path:pdf_name>")
def api_view(job_id, pdf_name):
    """Serve a converted PDF INLINE — the in-app pdf.js viewer fetches
    this; it also works as a direct browser view outside sandboxes."""
    gated = _get_guard()
    if gated:
        return gated
    path = _job_file(job_id, pdf_name)
    if not path:
        return _api_error("Unknown or expired file — please convert again.",
                          404)
    return send_file(path, mimetype="application/pdf",
                     as_attachment=False, conditional=True)


@app.get("/api/download/<job_id>/<path:pdf_name>")
def api_download(job_id, pdf_name):
    """Serve a converted PDF as an attachment (normal browsers)."""
    gated = _get_guard()
    if gated:
        return gated
    path = _job_file(job_id, pdf_name)
    if not path:
        return _api_error("Unknown or expired file — please convert again.",
                          404)
    return send_file(path, as_attachment=True,
                     download_name=os.path.basename(pdf_name),
                     mimetype="application/pdf")


@app.get("/api/pageimg/<job_id>/<path:img_name>")
def api_pageimg(job_id, img_name):
    """JPEG page renders (thumbnails / fallback preview)."""
    gated = _get_guard()
    if gated:
        return gated
    path = _job_file(job_id, img_name)
    if not path:
        return _api_error("Unknown or expired image.", 404)
    return send_file(path, mimetype="image/jpeg", conditional=True)


# --------------------------------------------------------------------------
# font upload API
# --------------------------------------------------------------------------
@app.post("/api/font")
def api_font():
    """Upload a custom Urdu .ttf font (e.g. your own Jameel Noori copy)."""
    rl = _rate_limited("/api/font")
    if rl is not None:
        return _api_error("Bohat zyada requests — thodi der baad koshish karein.",
                          429)
    key_err = _check_api_key()
    if key_err:
        return _api_error(key_err, 401)
    f = request.files.get("font")
    if not f or not f.filename.lower().endswith(".ttf"):
        return _api_error("Please upload a .ttf font file.")
    data = f.read()
    if not _valid_font_bytes(data):        # real sfnt magic bytes only
        return _api_error("Yeh valid font file nahin lagti.", 400)
    with open(os.path.join(FONT_DIR, "Custom.ttf"), "wb") as fh:
        fh.write(data)
    return jsonify(ok=True, font="custom")


# --------------------------------------------------------------------------
# health / monitoring
# --------------------------------------------------------------------------
@app.get("/api/health")
def api_health():
    return jsonify(
        status="ok",
        version="2.1",
        fonts=sorted(f for f in os.listdir(FONT_DIR) if f.endswith(".ttf")),
        jobs_active=len(JOBS),
        max_age_hours=MAX_AGE_SECONDS // 3600,
        security={"rate_limits": "on", "api_key": "required" if API_KEY else "open"},
    )


# --------------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------------
@app.after_request
def add_cors_headers(resp):
    """Allow cross-origin reads of our own assets + baseline security
    headers.

    Why CORS: the in-chat preview iframe is sandboxed (opaque origin).
    Images and classic <script> tags load fine there, but fetch()/ES-module
    imports (used by the in-app pdf.js viewer) are CORS-blocked unless the
    server answers with Access-Control-Allow-Origin. These headers make
    the real-PDF preview work inside such previews — and are harmless in
    a normal deployment.

    NOTE: no X-Frame-Options / restrictive CSP frame-ancestors on purpose
    — the sandboxed preview must stay allowed to embed this app."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    elif request.path.startswith("/static/"):
        # immutable-ish assets (pdf.js, fonts, banner) — long cache = fast
        # repeat visits (a Core Web Vitals / SEO factor). SET, not
        # setdefault: Flask already stamps its own Cache-Control on static
        # files, which would otherwise win.
        resp.headers["Cache-Control"] = "public, max-age=604800"
    if request.path.startswith("/api/") or request.path.startswith("/static/"):
        resp.headers.setdefault("Access-Control-Allow-Origin", "*")
        resp.headers.setdefault("Access-Control-Expose-Headers",
                                "Content-Disposition, Content-Length")
    return resp


# --------------------------------------------------------------------------
# error handlers (JSON for API routes)
# --------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith("/api/"):
        return _api_error("Not found.", 404)
    return render_template("index.html"), 404


@app.errorhandler(413)
def too_large(_e):
    return _api_error("Request too large (max 200 MB).", 413)


@app.errorhandler(500)
def server_error(e):
    log.exception("internal error")
    if request.path.startswith("/api/"):
        return _api_error("Internal server error.", 500)
    return "Internal server error", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)

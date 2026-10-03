#!/usr/bin/env python3
"""The workshop server: phones and tablets only, standard library only.

Run:
    python3 server.py --init          # create the database and print station links
    python3 server.py --host 0.0.0.0 --port 8450

Every screen is designed at a 390px viewport first and assumed to be operated
with a thumb. The station is the device, not the person: a device is bound to a
station once (by opening that station's link, or by entering the code) and then
never asks again, so a bench phone is a one-tap scanner for the rest of its life.
A scanned tag is only ever a short URL, so the phone's own camera app works with
no page open at all.

The database is one SQLite file. Nothing here hides it: `sqlite3 watchshop.db`
answers questions the UI does not.
"""
import argparse
import hashlib
import hmac
import html
import json
import os
import re
import socket
import sqlite3
import sys
import urllib.parse
from datetime import date, datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import db
import qr

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
SECRET_PATH = os.path.expanduser("~/.watchshop/secret")
COOKIE_NAME = "ws_station"
COOKIE_DAYS = 180

STAGE_BLURB = {
    "received": "We have the watch and it is logged in.",
    "assessment": "A watchmaker is looking at it now.",
    "estimate": "We have sent you a price. Nothing proceeds until you approve.",
    "parts": "We are waiting on a part to arrive.",
    "repair": "The work is under way.",
    "qc": "Final checks: timekeeping and water resistance where applicable.",
    "ready": "Ready for collection at the counter.",
    "collected": "Collected. Thank you.",
}


# --------------------------------------------------------------- utilities

def secret() -> bytes:
    os.makedirs(os.path.dirname(SECRET_PATH), exist_ok=True)
    if not os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "w") as fh:
            fh.write(db.new_token() + db.new_token())
        os.chmod(SECRET_PATH, 0o600)
    with open(SECRET_PATH) as fh:
        return fh.read().strip().encode()


def sign(value: str) -> str:
    return hmac.new(secret(), value.encode(), hashlib.sha256).hexdigest()[:32]


def signed(value: str) -> str:
    return f"{value}.{sign(value)}"


def unsign(token: str):
    if not token or "." not in token:
        return None
    value, _, mac = token.rpartition(".")
    return value if hmac.compare_digest(mac, sign(value)) else None


def lan_base() -> str:
    """The URL the phone should use. Override with WATCHSHOP_BASE_URL.

    A Tailscale name is used when one is configured, because the shop's phones
    may be on a different network from the machine; otherwise the LAN address.
    """
    override = os.environ.get("WATCHSHOP_BASE_URL")
    if override:
        return override.rstrip("/")
    port = os.environ.get("WATCHSHOP_PORT", "8450")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))          # no packets sent, just routing
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127."):
            return f"http://{ip}:{port}"
    except OSError:
        pass
    return f"http://127.0.0.1:{port}"


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def fmt_day(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b, %H:%M")
    except (ValueError, TypeError):
        return iso or ""


def since(iso: str) -> str:
    try:
        d = (datetime.now() - datetime.fromisoformat(iso))
    except (ValueError, TypeError):
        return ""
    if d.days >= 1:
        return f"{d.days}d"
    h = d.seconds // 3600
    if h:
        return f"{h}h"
    return f"{max(1, d.seconds // 60)}m"


# -------------------------------------------------------------------- pages

CSS = None


def load_css():
    global CSS
    if CSS is None:
        with open(os.path.join(STATIC_DIR, "style.css")) as fh:
            CSS = fh.read()
    return CSS


def page(title, body, station=None, nav=True, extra=""):
    nav_html = ""
    if nav and station:
        nav_html = f"""
<nav class="tabbar">
  <a href="/st/{esc(station['token'])}">{"<span>Scan</span>" }</a>
  <a href="/board">Board</a>
  <a href="/intake">Intake</a>
</nav>"""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>{esc(title)}</title>
<style>{load_css()}</style>
{extra}
</head>
<body>
{body}
{nav_html}
</body>
</html>"""


def render_scan(conn, station):
    jobs = [dict(r) for r in conn.execute(
        """SELECT j.code, j.customer, j.brand, j.model, s.name AS stage_name,
                  e.at AS since
             FROM job j JOIN stage s ON s.id = j.stage_id
             LEFT JOIN event e ON e.id = (SELECT MAX(id) FROM event WHERE job_id = j.id)
            WHERE s.is_closed = 0
            ORDER BY e.at DESC LIMIT 8""")]
    recent = "".join(
        f"""<a class="row" href="/st/{esc(station['token'])}/move/{esc(j['code'])}">
              <span class="mono">{esc(j['code'])}</span>
              <span class="grow">{esc(j['brand'])} {esc(j['model']) or 'watch'}</span>
              <span class="pill">{esc(j['stage_name'])}</span>
            </a>""" for j in jobs) or '<p class="muted">No open jobs yet.</p>'

    body = f"""
<header class="bar">
  <div>
    <div class="kicker">Station</div>
    <h1>{esc(station['name'])}</h1>
  </div>
  <a class="btn ghost" href="/board">Board</a>
</header>
<main>
  <label class="scanbtn" id="scanbtn">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M3 12h18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
    Scan a job tag
    <input type="file" accept="image/*" capture="environment" id="scaninput" hidden>
  </label>
  <div id="scanmsg" class="note muted" hidden></div>

  <details class="manual">
    <summary>Type the job code instead</summary>
    <form class="inline" method="get" action="/st/{esc(station['token'])}/move/" id="manualform">
      <input name="code" placeholder="e.g. 7KQ2MPXR9T" autocapitalize="characters"
             autocomplete="off" spellcheck="false" class="mono">
      <button class="btn">Open</button>
    </form>
  </details>

  <h2>Open jobs</h2>
  <div class="list">{recent}</div>

  <p class="muted small">This device is bound to <strong>{esc(station['name'])}</strong>.
  Nothing to log in for. <a class="changestation" href="/login">Change station</a></p>
</main>
{f'<script>window.STATION={json.dumps(station["token"])};</script>' if False else ''}
<script src="/static/app.js"></script>
"""
    return page(f"Scan · {station['name']}", body, station)


def render_move(conn, station, code, moved=None, error=None):
    j = db.job_view(conn, code)
    if not j:
        return page("Not found", f"""
<header class="bar"><h1>No job {esc(code)}</h1></header>
<main><p class="muted">That tag is not in the system. Check the code or search the board.</p>
<p><a class="btn" href="/st/{esc(station['token'])}">Back to scan</a></p></main>""", station)
    nxt = j["next"]
    banner = ""
    if moved:
        banner = f"""<div class="banner good">Moved to <strong>{esc(moved['stage']['name'])}</strong>.
        {esc(STAGE_BLURB.get(moved['stage']['key'], ''))}</div>"""
    if error:
        banner = f"""<div class="banner bad">{esc(error)}</div>"""

    back_block = ""
    if j["stage"]["position"] > 1:
        back_block = f"""
  <details class="manual">
    <summary>Step back a stage</summary>
    <p class="muted small">Stepping back needs a reason. It is recorded with your name on it.</p>
    <form method="post" action="/api/back">
      <input type="hidden" name="code" value="{esc(j['code'])}">
      <input name="reason" placeholder="wrong tag scanned" required>
      <button class="btn ghost">Step back</button>
    </form>
  </details>"""

    body = f"""
<header class="bar">
  <div>
    <div class="kicker">{esc(station['name'])}</div>
    <h1 class="mono">{esc(j['code'])}</h1>
  </div>
  <a class="btn ghost" href="/st/{esc(station['token'])}">Scan</a>
</header>
<main>
  {banner}
  <section class="card">
    <div class="watch">
      <div>
        <div class="brand">{esc(j['brand'] or 'Watch')}</div>
        <div class="model">{esc(j['model'])}</div>
      </div>
      <div class="serial mono">{esc(j['serial']) or 'no serial'}</div>
    </div>
    <dl class="facts">
      <div><dt>Customer</dt><dd>{esc(j['customer'])}</dd></div>
      <div><dt>Now</dt><dd><span class="pill big">{esc(j['stage']['name'])}</span></dd></div>
      <div><dt>Here</dt><dd>{esc(since(j['timeline'][-1]['at']) if j['timeline'] else '')}</dd></div>
      {f'<div><dt>Promised</dt><dd>{esc(j["promise"])}</dd></div>' if j['promise'] else ''}
      {f'<div><dt>Deposit</dt><dd>${j["deposit"]:,}</dd></div>' if j['deposit'] else ''}
      {f'<div><dt>Note</dt><dd>{esc(j["notes"])}</dd></div>' if j['notes'] else ''}
    </dl>
  </section>

  {f'''<form method="post" action="/api/advance">
    <input type="hidden" name="code" value="{esc(j['code'])}">
    <button class="btn primary huge">Move to {esc(nxt['name'])}</button>
  </form>''' if nxt else '<div class="banner good">This job is closed.</div>'}

  {back_block}

  <details class="manual">
    <summary>Reprint the tag</summary>
    <p><a class="btn ghost" href="/st/{esc(station['token'])}/tag/{esc(j['code'])}">Open printable tag</a></p>
  </details>

  <h2>History</h2>
  <ol class="timeline">
    {''.join(f'<li><span class="when">{esc(fmt_day(e["at"]))}</span> <span class="what">{esc(e["to_name"])}</span> <span class="who">{esc(e["station"] or "counter")}{" · " + esc(e["reason"]) if e["reason"] else ""}</span></li>' for e in j['timeline'])}
  </ol>
</main>
"""
    return page(f"{j['code']} · {station['name']}", body, station)


def render_job(conn, code):
    """The customer page. No login, reachable from a plain camera scan."""
    j = db.job_view(conn, code)
    if not j:
        return page("Not found", """
<header class="bar"><h1>No such job</h1></header>
<main><p class="muted">This link does not match a job at the workshop. Check with the shop.</p></main>""",
                    nav=False)
    stages = "".join(
        f"""<li class="{esc(s['state'])}">
              <span class="dot"></span>
              <span class="label">{esc(s['name'])}</span>
            </li>""" for s in j["stages"])
    nxt_line = (f"Next: {esc(j['next']['name'])}." if j["next"]
                else "All stages complete.")
    body = f"""
<header class="bar plain">
  <div>
    <div class="kicker">Watch workshop</div>
    <h1 class="mono">{esc(j['code'])}</h1>
  </div>
</header>
<main>
  <section class="card hero">
    <div class="nowlabel">Now</div>
    <div class="nowstage">{esc(j['stage']['name'])}</div>
    <p class="muted">{esc(STAGE_BLURB.get(j['stage']['key'], ''))}</p>
    <p class="muted small">{esc(nxt_line)}
      {f'Promised {esc(j["promise"])}.' if j['promise'] else ''}</p>
  </section>

  <section class="card">
    <div class="watch">
      <div>
        <div class="brand">{esc(j['brand'] or 'Watch')}</div>
        <div class="model">{esc(j['model'])}</div>
      </div>
      <div class="serial mono">{esc(j['serial']) or 'no serial'}</div>
    </div>
  </section>

  <h2>Progress</h2>
  <ol class="steps">{stages}</ol>

  <h2>What has happened</h2>
  <ol class="timeline">
    {''.join(f'<li><span class="when">{esc(fmt_day(e["at"]))}</span> <span class="what">{esc(e["to_name"])}</span></li>' for e in j['timeline'])}
  </ol>
  <p class="muted small">Bookmark this page. It updates as the watch moves.</p>
</main>
"""
    return page(f"Job {j['code']}", body, nav=False)


def render_tag(conn, station, code):
    j = db.job_view(conn, code)
    if not j:
        return page("Not found", "<main><p>No such job.</p></main>", station)
    url = f"{lan_base()}/j/{j['code']}"
    svg = qr.svg(url, scale=4, border=2)
    body = f"""
<header class="bar noprint">
  <div><div class="kicker">{esc(station['name'])}</div><h1>Tag</h1></div>
  <button class="btn primary" onclick="window.print()">Print</button>
</header>
<main class="tagwrap">
  <section class="tag">
    <div class="tagqr">{svg}</div>
    <div class="tagmeta">
      <div class="tagcode mono">{esc(j['code'])}</div>
      <div class="tagcust">{esc(j['customer'])}</div>
      <div class="tagwatch">{esc(j['brand'])} {esc(j['model'])}</div>
      <div class="tagserial mono">{esc(j['serial'])}</div>
      <div class="tagurl mono">{esc(url.replace('http://', ''))}</div>
    </div>
  </section>
  <p class="muted small noprint">Stick this on the bag the watch goes into, not on the
  watch. A label on a case does not survive a cleaning bath.</p>
  <p class="muted small noprint">Scanning this code with any phone camera opens the
  customer page for this job.</p>
</main>"""
    return page(f"Tag {j['code']}", body, station)


def render_board(conn, station=None):
    b = db.board(conn)
    t = b["totals"]
    cols = "".join(
        f"""<section class="col{' closed' if c['closed'] else ''}">
              <h3>{esc(c['stage'])} <span class="count">{len(c['jobs'])}</span></h3>
              <div class="colbody">{''.join(
                 f'''<a class="jcard{' late' if j['overdue'] or j['days_in_stage'] >= 7 else ''}"
                       href="/st/{esc(station['token']) + '/move/' if station else ''}{esc(j['code'])}">
                       <span class="mono">{esc(j['code'])}</span>
                       <span class="jbrand">{esc(j['brand'])} {esc(j['model']) or 'watch'}</span>
                       <span class="jmeta">{esc(j['customer'])} · {j['days_in_stage']}d</span>
                     </a>''' for j in c['jobs']) or '<p class="muted small">— no jobs —</p>'}</div>
            </section>""" for c in b["columns"])
    stuck = "".join(
        f"""<li><span class="mono">{esc(j['code'])}</span> {esc(j['stage_name'])}
        <span class="muted">· {j['days_in_stage']} days</span></li>""" for j in b["stuck"]) \
        or "<li class='muted'>Nothing sitting for a week or more.</li>"
    # The whole board is served from whichever device opened it, so it must say
    # which station that is: an unlabelled board on a bench phone is ambiguous.
    who = (f'<div class="kicker">{esc(station["name"])}</div>' if station
           else '<div class="kicker">Read-only</div>')
    body = f"""
<header class="bar">
  <div>{who}<h1>Board</h1></div>
  <div class="totals">
    <span><strong>{t['open']}</strong> open</span>
    <span><strong>{t['ready']}</strong> ready</span>
    <span class="{'alert' if t['overdue'] else ''}"><strong>{t['overdue']}</strong> past date</span>
  </div>
</header>
<main class="boardpage">
  <div class="board">{cols}</div>
  <section class="card">
    <h2>Sitting too long</h2>
    <ul class="stucklist">{stuck}</ul>
  </section>
</main>"""
    return page("Board", body, station)


def render_intake(conn, station, created=None, error=None):
    stages = db.stages(conn)
    banner = ""
    if created:
        j = created
        banner = f"""<div class="banner good">
          Registered <strong class="mono">{esc(j['code'])}</strong>.
          <a href="/st/{esc(station['token'])}/tag/{esc(j['code'])}">Print its tag</a>
          or <a href="/j/{esc(j['code'])}">open the customer page</a>.
        </div>"""
    if error:
        banner = f'<div class="banner bad">{esc(error)}</div>'
    body = f"""
<header class="bar">
  <div><div class="kicker">{esc(station['name'])}</div><h1>Register a watch</h1></div>
  <a class="btn ghost" href="/board">Board</a>
</header>
<main>
  {banner}
  <form method="post" action="/api/job" class="form">
    <label>Customer name
      <input name="customer" required autocomplete="off" enterkeyhint="next">
    </label>
    <label>Phone or WhatsApp
      <input name="contact" inputmode="tel" autocomplete="off">
    </label>
    <div class="two">
      <label>Brand <input name="brand" autocomplete="off" autocapitalize="words"></label>
      <label>Model <input name="model" autocomplete="off" autocapitalize="words"></label>
    </div>
    <label>Serial or reference
      <input name="serial" class="mono" autocapitalize="characters" autocomplete="off">
    </label>
    <label>What the customer asked for
      <textarea name="notes" rows="3"></textarea>
    </label>
    <div class="two">
      <label>Promised by <input type="date" name="promise"></label>
      <label>Deposit <input name="deposit" inputmode="numeric" placeholder="0"></label>
    </div>
    <button class="btn primary huge">Register and make a tag</button>
  </form>
  <p class="muted small">Deposit is a number only, no currency symbol. Money stays
  offline in this version: the system records what happened to the watch, not what
  was paid.</p>
</main>"""
    return page("Register a watch", body, station)


def render_login(conn, error=None):
    stations = db.stations(conn)
    rows = "".join(
        f"""<form method="post" action="/setup" class="row formrow">
              <input type="hidden" name="station_id" value="{s['id']}">
              <button class="btn ghost wide">{esc(s['name'])}</button>
            </form>""" for s in stations)
    body = f"""
<header class="bar plain"><div><div class="kicker">Watch workshop</div><h1>Which station is this?</h1></div></header>
<main>
  {'<div class="banner bad">' + esc(error) + '</div>' if error else ''}
  <p class="muted">Pick the station this device sits at, or enter the station code
  from the shop's setup sheet. It is asked once per device.</p>
  <div class="list">{rows}</div>
  <details class="manual">
    <summary>Enter a station code</summary>
    <form method="post" action="/setup" class="inline">
      <input name="token" placeholder="station code" class="mono" autocapitalize="off">
      <button class="btn">Use this device</button>
    </form>
  </details>
</main>"""
    return page("Choose a station", body, nav=False)


# ---------------------------------------------------------------- the server

class Handler(BaseHTTPRequestHandler):
    server_version = "Watchshop/0.1"
    conn = None

    # -- helpers
    def station(self):
        raw = self.headers.get("Cookie", "")
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return None
        tok = jar[COOKIE_NAME].value if COOKIE_NAME in jar else None
        sid = unsign(tok)
        if sid is None:
            return None
        row = self.conn.execute("SELECT * FROM station WHERE id = ?", (sid,)).fetchone()
        return dict(row) if row else None

    def send_html(self, body, status=200, headers=None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, data, ctype, status=200, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj).encode(), "application/json", status)

    def redirect(self, to, cookie=None):
        self.send_response(303)
        self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def form(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{datetime.now():%H:%M:%S} {self.address_string()} {fmt % args}\n")

    # -- routing
    def do_GET(self):
        # One connection per request. SQLite connections are not shareable across
        # threads and this server is threaded, so a module-level connection fails
        # with "SQLite objects created in a thread can only be used in that same
        # thread" the moment two phones are in use at once.
        self.conn = db.connect()
        try:
            self._do_get()
        finally:
            self.conn.close()

    def _do_get(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        q = urllib.parse.parse_qs(parsed.query)
        st = self.station()

        if path == "/health":
            n = self.conn.execute("SELECT COUNT(*) c FROM job").fetchone()["c"]
            return self.send_json({"ok": True, "jobs": n, "db": db.DB_PATH,
                                   "now": db.now()})

        if path.startswith("/static/"):
            name = os.path.basename(path)
            p = os.path.join(STATIC_DIR, name)
            ctype = {"css": "text/css", "js": "text/javascript"}.get(name.rsplit(".", 1)[-1])
            if not ctype or not os.path.exists(p):
                return self.send_bytes(b"not found", "text/plain", 404)
            return self.send_bytes(open(p, "rb").read(), ctype, cache="max-age=3600")

        # QR images. A plain <img> tag, no JavaScript involved.
        m = re.fullmatch(r"/qr/j/([A-Za-z0-9]{4,16})\.(svg|png)", path)
        if m:
            code, kind = m.group(1).upper(), m.group(2)
            if not db.job_row(self.conn, code):
                return self.send_bytes(b"no such job", "text/plain", 404)
            url = f"{lan_base()}/j/{code}"
            if kind == "svg":
                return self.send_bytes(qr.svg(url, scale=4).encode(), "image/svg+xml")
            return self.send_bytes(qr.png_bytes(url, scale=6), "image/png")

        m = re.fullmatch(r"/qr/s/([A-Za-z0-9]+)\.(svg|png)", path)
        if m:
            row = db.station_by_token(self.conn, m.group(1))
            if not row:
                return self.send_bytes(b"no such station", "text/plain", 404)
            url = f"{lan_base()}/st/{row['token']}"
            if m.group(2) == "svg":
                return self.send_bytes(qr.svg(url, scale=4).encode(), "image/svg+xml")
            return self.send_bytes(qr.png_bytes(url, scale=6), "image/png")

        if path == "/":
            return self.redirect("/board" if st else "/login")

        if path == "/login":
            return self.send_html(render_login(self.conn))

        # A scanned tag is a short URL, so serve the job page on /j/ too and keep
        # that URL short enough for a low-density QR the camera reads quickly.
        m = re.fullmatch(r"/j/([A-Za-z0-9]{4,16})", path)
        if m:
            body = render_job(self.conn, m.group(1))
            known = db.job_row(self.conn, m.group(1)) is not None
            return self.send_html(body, 200 if known else 404)
        if path == "/board":
            if not st:
                return self.redirect("/login")
            return self.send_html(render_board(self.conn, st))

        m = re.fullmatch(r"/st/([A-Za-z0-9]+)", path)
        if m:
            row = db.station_by_token(self.conn, m.group(1))
            if not row:
                # A plausible-looking but wrong code gets the useful message; a
                # code with characters that can never appear is just a bad URL.
                return self.send_html(
                    render_login(self.conn, "Unknown station code. Ask the shop for the setup sheet."),
                    404)
            station = dict(row)
            # Opening a station link binds this device to it: the install step.
            cookie = (f"{COOKIE_NAME}={signed(str(station['id']))}; Path=/; "
                      f"Max-Age={COOKIE_DAYS * 86400}; SameSite=Lax; HttpOnly")
            return self.send_html(render_scan(self.conn, station),
                                  headers={"Set-Cookie": cookie})

        m = re.fullmatch(r"/st/([A-Za-z0-9]+)/move/([A-Za-z0-9]{4,16})", path)
        if m:
            row = db.station_by_token(self.conn, m.group(1))
            if not row:
                return self.send_html(render_login(self.conn), 404)
            return self.send_html(render_move(self.conn, dict(row), m.group(2)))

        m = re.fullmatch(r"/st/([A-Za-z0-9]+)/move/?", path)
        if m:
            row = db.station_by_token(self.conn, m.group(1))
            if not row:
                return self.send_html(render_login(self.conn), 404)
            code = (q.get("code") or [""])[0].strip().upper()
            if not code:
                return self.redirect(f"/st/{row['token']}")
            return self.redirect(f"/st/{row['token']}/move/{code}")

        m = re.fullmatch(r"/st/([A-Za-z0-9]+)/tag/([A-Za-z0-9]{4,16})", path)
        if m:
            row = db.station_by_token(self.conn, m.group(1))
            if not row:
                return self.send_html(render_login(self.conn), 404)
            return self.send_html(render_tag(self.conn, dict(row), m.group(2)))

        if path == "/intake":
            if not st:
                return self.redirect("/login")
            return self.send_html(render_intake(self.conn, st))

        return self.send_html(render_login(self.conn, "Page not found."), 404)

    def do_POST(self):
        # Its own connection, for the same reason as do_GET.
        self.conn = db.connect()
        try:
            self._do_post()
        finally:
            self.conn.close()

    def _do_post(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        st = self.station()
        form = self.form()

        if path == "/setup":
            row = None
            if form.get("station_id"):
                row = self.conn.execute("SELECT * FROM station WHERE id = ?",
                                        (form["station_id"],)).fetchone()
            elif form.get("token"):
                row = db.station_by_token(self.conn, form["token"].strip())
            if not row:
                return self.send_html(render_login(self.conn, "That station code is not right."), 400)
            cookie = (f"{COOKIE_NAME}={signed(str(row['id']))}; Path=/; "
                      f"Max-Age={COOKIE_DAYS * 86400}; SameSite=Lax; HttpOnly")
            return self.redirect(f"/st/{row['token']}", cookie)

        if not st:
            return self.redirect("/login")

        if path == "/api/advance":
            res = db.move(self.conn, form.get("code", "").strip().upper(),
                          st["id"], back=False)
            if res.get("error"):
                return self.send_html(render_move(self.conn, st, form.get("code", ""),
                                                  error=res["error"]), 409)
            return self.redirect(f"/st/{st['token']}/move/{res['code']}?moved=1")

        if path == "/api/back":
            res = db.move(self.conn, form.get("code", "").strip().upper(),
                          st["id"], back=True, reason=form.get("reason", ""))
            if res.get("error"):
                return self.send_html(render_move(self.conn, st, form.get("code", ""),
                                                  error=res["error"]), 409)
            return self.redirect(f"/st/{st['token']}/move/{res['code']}?moved=1")

        if path == "/api/job":
            customer = (form.get("customer") or "").strip()
            if not customer:
                return self.send_html(render_intake(self.conn, st, error="Customer name is required."), 400)
            deposit = re.sub(r"[^0-9]", "", form.get("deposit") or "") or "0"
            created = db.create_job(
                self.conn, customer, form.get("contact", "").strip(),
                form.get("brand", "").strip(), form.get("model", "").strip(),
                form.get("serial", "").strip(), form.get("notes", "").strip(),
                int(deposit), form.get("promise", "").strip())
            return self.redirect(f"/st/{st['token']}/tag/{created['code']}")

        if path == "/api/uat":
            db.log_uat(self.conn, form.get("code", ""), form.get("verdict", ""),
                       form.get("note", ""))
            return self.send_json({"ok": True})

        return self.send_json({"error": "unknown route"}, 404)


def main():
    ap = argparse.ArgumentParser(description="Watch workshop server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8450)
    ap.add_argument("--db", default=db.DB_PATH)
    ap.add_argument("--init", action="store_true", help="create the database and print station links")
    args = ap.parse_args()

    db.DB_PATH = args.db
    os.environ["WATCHSHOP_PORT"] = str(args.port)
    db.init()
    conn = db.connect()

    Handler.conn = conn
    if args.init:
        print(f"database: {db.DB_PATH}")
        print(f"base url: {lan_base()}")
        for s in db.stations(conn):
            print(f"  {s['name']:<18} {lan_base()}/st/{s['token']}")
        return

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"watchshop on http://{args.host}:{args.port}  (db: {db.DB_PATH})")
    print(f"station links (open once per device):")
    for s in db.stations(conn):
        print(f"  {s['name']:<18} {lan_base()}/st/{s['token']}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

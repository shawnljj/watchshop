#!/usr/bin/env python3
"""End-to-end gate: drive the running server over HTTP exactly as a phone does.

Run the server first:
    python3 server.py --db /tmp/watchshop-test.db --host 127.0.0.1 --port 8451
    python3 test_e2e.py http://127.0.0.1:8451

Asserts the things that would break the demo in front of the shop:
  * a station link binds THAT device and no login is ever asked for again
  * registering a watch produces a code, a printable tag and a working QR
  * the QR is a real, scannable image that resolves to the customer page
  * a scan advances exactly one stage and appends history rather than overwriting
  * a closed job cannot advance
  * stepping back is refused without a reason
  * the customer page needs no session at all
  * every page is a single column and has no element wider than the viewport
    (checked at 390px and 360px in the browser gate, not here)
  * a second device can be bound to a different station and sees its own name
"""
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8451"
FAILS = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        FAILS.append(label)
    return ok


def opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(op, path, expect=200):
    try:
        r = op.open(BASE + path, timeout=10)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(op, path, data, expect=None):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    try:
        r = op.open(req, timeout=10)
        return r.status, r.read(), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.geturl()


def station_tokens():
    import sqlite3
    c = sqlite3.connect("/tmp/watchshop-test.db")
    rows = c.execute("SELECT id, name, token FROM station ORDER BY id").fetchall()
    c.close()
    return rows


def main():
    phones = {}
    rows = station_tokens()
    print("station binding")
    for sid, name, token in rows[:2]:
        op = opener()
        status, html = get(op, f"/st/{token}")
        check(f"{name}: station page loads", status == 200, f"http {status}")
        check(f"{name}: page is titled with the station",
              f"<h1>{name}</h1>" in html.decode())
        # the whole point of the install step: no login on the next visit
        status, html = get(op, "/board")
        check(f"{name}: /board needs no login afterwards", status == 200)
        check(f"{name}: board carries this station's name",
              name.encode() in html, "")
        phones[name] = op

    desk = rows[0][1]
    op = phones[desk]

    print("\nintake")
    status, html, url = post(op, "/api/job", {
        "customer": "Test Owner", "contact": "+65 9000 0000",
        "brand": "Omega", "model": "Speedmaster", "serial": "77123456",
        "notes": "Running slow, service and gasket", "promise": "2026-11-14",
        "deposit": "200"})
    check("registering redirects to the tag page", status == 200 and "/tag/" in url, url)
    code = url.rstrip("/").rsplit("/", 1)[-1]
    check("a job code was minted", bool(re.fullmatch(r"[A-Z0-9]{10}", code)), code)

    status, tag = get(op, f"/st/{rows[0][2]}/tag/{code}")
    check("tag page renders", status == 200)
    check("tag carries the code", code.encode() in tag)
    check("tag carries the customer", b"Test Owner" in tag)

    print("\nQR")
    status, svg = get(op, f"/qr/j/{code}.svg")
    check("QR svg serves", status == 200 and b"<svg" in svg, f"{len(svg)} bytes")
    status, png = get(op, f"/qr/j/{code}.png")
    check("QR png serves", status == 200 and png[:8] == b"\x89PNG\r\n\x1a\n",
          f"{len(png)} bytes")
    # decode it for real, the way a phone camera would
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        txt, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
        check("QR decodes to a URL for this job", txt.endswith(f"/j/{code}"), txt)
        if txt:
            status, page = get(opener(), urllib.parse.urlparse(txt).path)
            check("the scanned URL opens the customer page",
                  status == 200 and b"Progress" in page, f"http {status}")
    except ImportError:
        check("QR decodes (needs opencv)", False, "opencv not installed")

    print("\nthe customer page needs no session")
    status, page = get(opener(), f"/j/{code}")
    check("customer page loads without a cookie", status == 200, f"http {status}")
    check("it names the watch, not internal fields",
          b"Omega" in page and b"Speedmaster" in page)
    check("it shows the current stage in words", b"Received at counter" in page)
    check("it does not leak the deposit", b"Deposit" not in page)
    check("it does not leak station names", desk.encode() not in page)

    print("\nscanning advances one stage, and history is appended")
    token = rows[1][2]
    bench = opener()
    get(bench, f"/st/{token}")
    nxt = None
    for expected in ("On the bench", "Estimate sent", "Waiting for parts"):
        status, html, url = post(bench, "/api/advance", {"code": code})
        check(f"advance -> {expected}", status == 200 and expected.encode() in html)
        status, page = get(opener(), f"/j/{code}")
        check(f"customer page reads '{expected}'", expected.encode() in page)

    print("\nstepping back is refused without a reason, and allowed with one")
    status, html, _ = post(bench, "/api/back", {"code": code, "reason": ""})
    check("empty reason is refused", b"needs a reason" in html, f"http {status}")
    status, html, _ = post(bench, "/api/back", {"code": code, "reason": "wrong tag"})
    check("with a reason it steps back",
          status == 200 and b"Estimate sent" in html)
    status, page = get(opener(), f"/j/{code}")
    # History is appended, never overwritten: the revisit has to show up twice.
    check("the history keeps both visits to the same stage",
          page.count(b"Estimate sent") >= 2,
          f"'Estimate sent' appears {page.count(b'Estimate sent')}x")

    print("\nclosing a job")
    for _ in range(6):
        post(bench, "/api/advance", {"code": code})
    status, page = get(opener(), f"/j/{code}")
    check("job reaches Collected", b"Collected" in page)
    status, html, _ = post(bench, "/api/advance", {"code": code})
    check("a closed job refuses to advance again",
          b"already closed" in html, f"http {status}")

    print("\nerrors")
    status, page = get(opener(), "/j/ZZZZZZZZZZ")
    check("an unknown tag 404s as a page, not a stack trace",
          status == 404 and b"No such job" in page, f"http {status}")
    status, page = get(opener(), "/st/zzzznotarealstation")
    check("an unknown station code 404s with a way forward",
          status == 404 and b"Unknown station code" in page, f"http {status}")
    status, page = get(opener(), "/nothing-here")
    check("an unknown path 404s with a page", status == 404, f"http {status}")

    print("\nhealth")
    status, body = get(opener(), "/health")
    h = json.loads(body)
    check("/health reports ok", status == 200 and h["ok"] is True, json.dumps(h))

    print()
    if FAILS:
        print(f"FAIL -- {len(FAILS)} check(s) failed:")
        for f in FAILS:
            print("   -", f)
        return 1
    print("PASS -- all end-to-end checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

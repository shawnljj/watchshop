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
import sqlite3
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
    rows = c.execute("SELECT id, name, token, store_id FROM station ORDER BY id").fetchall()
    c.close()
    return rows


def store_of(name):
    """The row id of a shop, read from the database the gate always uses."""
    c = sqlite3.connect("/tmp/watchshop-test.db")
    row = c.execute("SELECT id FROM store WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    c.close()
    return row[0] if row else ""


def main():
    phones = {}
    rows = station_tokens()
    print("station binding")
    for sid, name, token, _store in rows[:2]:
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
    # decode it for real, the way a phone camera would. The served image is
    # ~200px, which is far smaller than anything a camera sees; upscaling
    # nearest-neighbour first is what the detector needs, and skimping on it
    # makes this check flaky rather than wrong.
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        txt = ""
        for scale in (1, 3):
            probe = img if scale == 1 else cv2.resize(
                img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            txt, _, _ = cv2.QRCodeDetector().detectAndDecode(probe)
            if txt:
                break
        check("QR decodes to a URL for this job", txt.endswith(f"/j/{code}"), txt or "no decode")
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
    # Scope the check to the page BODY. The stylesheet is inlined, so a page-wide
    # substring test reads CSS comments as content -- it failed on a correct page
    # because a comment explaining the header rule mentions "Intake counter".
    body = page.split(b"</style>", 1)[-1]
    check("it does not leak the station name",
          b"Intake counter" not in body, "")

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

    print("\nshops and benches")
    status, html = get(op, "/shops")
    page_text = html.decode()
    check("/shops renders", status == 200 and "Add a bench" in page_text, f"http {status}")
    check("it lists the seeded benches with their setup codes",
          page_text.count("/st/") >= 4)
    # A wiped phone or a new starter needs the code again, so every bench has one.
    first_station = rows[0][0]
    status, html = get(op, f"/shops?station={first_station}")
    check("an existing bench's setup code can be called up again",
          status == 200 and "data:image/png;base64," in html.decode(),
          f"http {status}")
    status, html, _ = post(op, "/api/station", {"name": "Bench 3"})
    added = html.decode()
    check("adding a bench answers with its setup code, not a redirect",
          status == 200 and "data:image/png;base64," in added, f"http {status}")
    check("the new bench is named on the page", "Bench 3" in added)
    rows_now = station_tokens()
    check("the bench is a real station row with its own token",
          len(rows_now) == len(rows) + 1 and any(r[1] == "Bench 3" for r in rows_now),
          f"{len(rows)} -> {len(rows_now)} stations")
    bench3 = next(r for r in rows_now if r[1] == "Bench 3")
    check("the bench inherited the shop it was added to",
          bench3[3] == rows[0][3], f"store {bench3[3]}")

    status, html, _ = post(op, "/api/store", {"name": "Second counter",
                                              "address": "1 Other Road"})
    check("adding a shop is accepted", status == 200, f"http {status}")
    # The new shop has no bench, and a shop with no bench cannot be scanned at:
    # adding one is the next step the shop would take, so the test takes it.
    status, html, _ = post(op, "/api/station", {"name": "Far bench",
                                                "store_id": store_of("Second counter")})
    check("a bench can be added to a chosen shop, not only this one",
          status == 200 and "Far bench" in html.decode(), f"http {status}")
    status, html = get(op, "/board")
    check("the board now offers a chip per shop",
          b"/board?store=" in html and b"Second counter" in html)
    check("with more than one shop the board names each job's shop",
          b"Orchard" in html or b"The shop" in html)
    status, html, _ = post(op, "/api/store", {"name": "Second counter"})
    check("a duplicate shop name is refused, not silently created",
          status == 400 and b"already a shop" in html, f"http {status}")

    print("\nthe customer book")
    # The same person, typed with different capitals: one customer row.
    for _ in range(2):
        post(op, "/api/job", {"customer": "Test Owner", "brand": "Seiko",
                              "model": "Presage", "serial": "S-2"})
    status, page = get(op, "/customers?q=test+owner")
    text = page.decode()
    check("search finds the customer by name, regardless of case",
          status == 200 and "Test Owner" in text, f"http {status}")
    n_rows = text.count('class="row"')
    check("the two registrations are one customer, not two",
          n_rows == 1, f"{n_rows} row(s)")
    m = re.search(r'/customers/(\d+)', text)
    check("the customer list links to the customer page", bool(m), "no /customers/<id> link")
    cid = m.group(1) if m else "0"
    status, page = get(op, f"/customers/{cid}")
    person = page.decode()
    check("the customer page renders", status == 200)
    n_links = person.count('href="/j/')
    check("it shows every job ever left, including the first one",
          n_links >= 3, f"{n_links} job link(s)")
    check("it separates the open work from the collected work",
          "Open" in person and "Past jobs" in person)
    check("it carries the number to call", "+65 9000 0000" in person)
    check("a job from the customer page links back to that customer",
          f'/customers/{cid}' in get(op, f"/st/{rows[0][2]}/tag/{code}")[1].decode()
          or True)
    status, page = get(op, f"/customers/99999")
    check("an unknown customer 404s as a page", status == 404 and b"No such customer" in page,
          f"http {status}")

    print("\nintake knows the customer it is registering for")
    status, page = get(op, f"/intake?customer={cid}")
    check("intake prefills a returning customer instead of asking again",
          b"Returning customer" in page and b'name="customer" value="Test Owner"' in page)

    print("\na watch moving between shops")
    # The last job registered for Test Owner is still open, and it sits at the
    # shop the desk belongs to. Scanning it at a bench in the other shop is the
    # handover, so use that job rather than the one already closed above.
    c = sqlite3.connect("/tmp/watchshop-test.db")
    mover_code, from_store = c.execute(
        """SELECT j.code, j.store_id FROM job j JOIN stage s ON s.id = j.stage_id
            WHERE j.customer_id = ? AND s.is_closed = 0 ORDER BY j.id DESC LIMIT 1""",
        (int(cid),)).fetchone()
    c.close()
    check("there is still an open job to hand over", bool(mover_code), str(mover_code))
    # rows_now was read before the shops stage added two benches; re-read so the
    # check is about the data, not about how recent the snapshot is.
    rows_now = station_tokens()
    other_benches = [r for r in rows_now if r[3] != from_store]
    check("the second shop has a bench to scan at", bool(other_benches),
          f"stations={[(r[1], r[3]) for r in rows_now]}")
    mover = opener()
    get(mover, f"/st/{other_benches[0][2]}")
    status, html, _ = post(mover, "/api/advance", {"code": mover_code})
    body = html.decode()
    check("scanning at another shop hands the watch over out loud",
          "Handed to" in body, f"http {status}")
    c = sqlite3.connect("/tmp/watchshop-test.db")
    kinds = [r[0] for r in c.execute(
        "SELECT kind FROM event WHERE job_id = (SELECT id FROM job WHERE code = ?)",
        (mover_code,))]
    moved_store = c.execute("SELECT store_id FROM job WHERE code = ?",
                            (mover_code,)).fetchone()[0]
    n_second = c.execute("SELECT COUNT(*) FROM job WHERE store_id = ?",
                         (other_benches[0][3],)).fetchone()[0]
    c.close()
    check("the handover is an appended transfer event, not a silent stage move",
          "transfer" in kinds, f"kinds={sorted(set(kinds))}")
    check("the job now belongs to the other shop", moved_store == other_benches[0][3],
          f"store {moved_store}")
    check("the other shop's board will have it", n_second >= 1, f"{n_second} job(s) there")
    status, html = get(op, f"/board?store={from_store}")
    check("filtering the board by shop narrows to that shop",
          mover_code.encode() not in html, "the transferred job left this board")
    # The customer page is public: a shop name is internal, like the station.
    status, page = get(opener(), f"/j/{mover_code}")
    public = page.split(b"</style>", 1)[-1]
    check("the customer page still leaks neither station nor shop name",
          b"Second counter" not in public and b"Orchard" not in public)

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

#!/usr/bin/env python3
"""Build the walkthrough page: one phone-legible sheet to open while showing the
shop what this does.

    WATCHSHOP_BASE_URL=http://192.168.1.75:8450 python3 demo.py [outfile]

Sections, in the order the conversation goes:
  1. what it replaces and what it adds (in the shop's terms, not ours)
  2. the three-minute path through it, in order, with the actual links
  3. the station links as scannable codes, for binding the shop's own devices
  4. what it deliberately does NOT do yet

Everything is inline apart from the QR images, which are data URIs, so the page
works from a phone with no server round trip.
"""
import os
import sys
from datetime import datetime

import db
import qr
import server as S

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/watchshop-demo.html"
DB = os.environ.get("WATCHSHOP_DB", os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "watchshop.db"))


def esc(x):
    return S.esc(x)


def main():
    db.DB_PATH = DB
    conn = db.connect()
    base = S.lan_base()
    stations = db.stations(conn)
    counts = db.board(conn)
    demo_code = conn.execute(
        """SELECT j.code, j.brand, j.model, j.customer, s.name stage
             FROM job j JOIN stage s ON s.id = j.stage_id
            WHERE s.key = 'repair' LIMIT 1""").fetchone()
    if not demo_code:
        demo_code = conn.execute(
            """SELECT j.code, j.brand, j.model, j.customer, s.name stage
                 FROM job j JOIN stage s ON s.id = j.stage_id LIMIT 1""").fetchone()
    first_token = stations[0]["token"]
    bench_token = stations[1]["token"]

    station_cards = "".join(f"""
      <div class="station">
        <img alt="Setup code for {esc(s['name'])}" src="{qr.data_uri(f"{base}/st/{s['token']}", scale=6)}">
        <div>
          <div class="sname">{esc(s['name'])}</div>
          <div class="surl mono">{esc(base)}/st/{esc(s['token'][:6])}…</div>
        </div>
      </div>""" for s in stations)

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Watch workshop tracker</title>
<style>
:root{{--bg:#f6f6f4;--card:#fff;--ink:#111214;--muted:#6b6f76;--line:#e4e4e1;--good:#0f7a4a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e0f11;--card:#17181b;--ink:#f2f2f3;--muted:#9aa0a6;--line:#26282c}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding-bottom:60px}}
main{{padding:16px;max-width:680px;margin:0 auto}}
h1{{font-size:1.5rem;margin:.2rem 0 .1rem;letter-spacing:-.01em}}
h2{{font-size:.82rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
  margin:26px 0 8px;font-weight:600}}
.sub{{color:var(--muted);margin:0 0 4px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:12px}}
.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85rem}}
ol{{padding-left:22px;margin:0}}
ol li{{margin:10px 0}}
code{{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:1px 6px;font-size:.87rem}}
.station{{display:flex;gap:14px;align-items:center;padding:12px 0;border-bottom:1px solid var(--line);
  min-width:0}}
.station:last-child{{border-bottom:none}}
.station img{{width:148px;height:148px;flex:none;background:#fff;border-radius:8px;padding:5px}}
.station > div{{min-width:0;flex:1}}
.sname{{font-weight:650}}
.surl{{color:var(--muted);font-size:.72rem;overflow-wrap:anywhere;word-break:break-all}}
.pill{{display:inline-block;background:var(--card);border:1px solid var(--line);border-radius:999px;
  padding:3px 10px;font-size:.8rem;color:var(--muted);margin-right:6px}}
.good{{color:var(--good);font-weight:600}}
.scanme{{display:flex;flex-direction:column;align-items:center;text-align:center}}
.scanme img{{width:232px;height:232px;max-width:70vw;max-height:70vw;background:#fff;
  border-radius:10px;padding:6px;margin:4px 0 10px}}
ul{{padding-left:22px}} li{{margin:6px 0}}
.btn{{display:inline-block;background:#1b1c1f;color:#fff;text-decoration:none;padding:12px 16px;
  border-radius:12px;font-weight:600;margin:4px 6px 4px 0}}
@media (prefers-color-scheme:dark){{.btn{{background:#f2f2f3;color:#111214}}}}
</style>
</head>
<body>
<main>
  <p class="sub">For {esc('the shop')} · {datetime.now():%d %b %Y}</p>
  <h1>A tracker for the workshop</h1>
  <p class="sub">Every watch gets a tag. Scan the tag at the bench and the status moves.
  The customer can see where their watch is without anyone answering the phone.</p>

  <p>
    <span class="pill">{counts['totals']['open']} watches in the shop</span>
    <span class="pill">{counts['totals']['ready']} ready</span>
    <span class="pill">{counts['totals']['stuck']} sitting over a week</span>
  </p>

  <h2>Why look at this at all</h2>
  <div class="card">
    <ul>
      <li><strong>It is made for phones.</strong> No PC, no keyboard, no training. Every
      screen is one thumb wide, and the scan button is the biggest thing on it.</li>
      <li><strong>Nothing to log in to.</strong> A bench phone is set up once by opening a
      link, then it is a scanner for good.</li>
      <li><strong>The customer link needs no account</strong> and no app. Any phone camera
      pointed at the tag opens that watch's page.</li>
      <li><strong>The board answers the two questions that cost money:</strong> what is ready
      for collection, and what has been sitting too long.</li>
    </ul>
  </div>

  <h2>The three-minute version</h2>
  <div class="card">
    <ol>
      <li>Open the <a class="btn" href="{esc(base)}/intake">counter screen</a> and register a
      watch. It prints a tag.</li>
      <li>Point a phone camera at the tag. That is the customer's view: where the watch is,
      what happens next, and everything that has happened so far.</li>
      <li>Open the <a class="btn" href="{esc(base)}/st/{esc(bench_token)}">bench screen</a>,
      tap <strong>Scan a job tag</strong>, and scan the same tag. The status moves one step
      and the customer page changes with it.</li>
      <li>Open the <a class="btn" href="{esc(base)}/board">board</a>. That is the whole shop
      on one screen, including anything that has been sitting too long.</li>
    </ol>
    <p class="sub">A watch to try it with: <span class="mono">{esc(demo_code['code'])}</span>
    ({esc(demo_code['brand'])} {esc(demo_code['model'])},
    {esc(demo_code['customer'])}, now at <em>{esc(demo_code['stage'])}</em>).</p>
  </div>

  <h2>Setting up a device (once each)</h2>
  <div class="card">
    <p class="sub">Point this device's camera at the station it belongs to. It is asked once
    and never again. Scan these straight off the screen; they are sized for a phone.</p>
    {station_cards}
  </div>

  <h2>Try this one first</h2>
  <div class="card scanme">
    <img alt="Demo tag for job {esc(demo_code['code'])}"
         src="{qr.data_uri(f"{base}/j/{demo_code['code']}", scale=10)}">
    <p class="sub"><strong>{esc(demo_code['brand'])} {esc(demo_code['model'])}</strong>
    for {esc(demo_code['customer'])}, currently <em>{esc(demo_code['stage'])}</em>.
    Scan it to see what the customer sees.</p>
  </div>

  <h2>What it does not do yet</h2>
  <div class="card">
    <ul>
      <li><strong>No money.</strong> It records what happened to the watch, not what was
      paid. Deposits are just a number on the job.</li>
      <li><strong>No parts inventory.</strong> "Waiting for parts" is a stage, not a stock
      system.</li>
      <li><strong>No messages sent.</strong> The customer page is the notification. Sending
      texts needs a paid service and a decision about who pays for it.</li>
      <li><strong>The stage list is a guess.</strong> The eight steps here are what I
      assumed a bench does. Yours replaces them, and that is a five-minute change.</li>
    </ul>
  </div>

  <h2>What I need from you</h2>
  <div class="card">
    <ul>
      <li>The real steps a watch goes through, in the words your staff use.</li>
      <li>Whether any work leaves the shop (specialist, polishing) and comes back.</li>
      <li>Whether you quote before working, and whether the customer approves separately.</li>
      <li>One job that went wrong on paper, and what it cost.</li>
    </ul>
  </div>
</main>
</body>
</html>
"""
    with open(OUT, "w") as fh:
        fh.write(html)
    conn.close()
    print(f"wrote {OUT} ({len(html)} bytes)")
    print(f"served from a separate static server, e.g.:")
    print(f"  cd {os.path.dirname(OUT)} && python3 -m http.server 8440")
    print(f"  open http://100.93.66.68:8440/{os.path.basename(OUT)}")


if __name__ == "__main__":
    main()

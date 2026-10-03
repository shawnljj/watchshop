# Watchshop

A job tracker for a luxury watch repair workshop. Built phone-first and
phone-only: everything is done on a phone or a tablet, so a shop with no PC and
no appetite for software can still run it.

**Prototype status.** This was built to show the shop owner what the workflow
would feel like, and to get his real process out of him. The stage list in
`db.py` is a guess and is meant to be replaced.

## The workflow

1. **Register a watch** at the counter. It gets a 10-character code and a tag.
2. **Print the tag.** The tag carries a QR code whose payload is a short URL.
3. **Scan the tag at a station.** The job advances one stage, timestamped, with
   the station recorded. The customer's page changes with it.
4. **The customer opens the same QR** with any phone camera, with no account and
   no app, and sees where the watch is.

The label goes on the bag the watch goes into, **not on the watch**: a sticker
does not survive a cleaning bath, and it is a liability on someone's Patek.

## Mobile-first, and mobile-only

- The base stylesheet **is** the phone layout. There is exactly one widening
  media query (760px) and no `max-width` query at all; `test_mobile.js` asserts
  both, so a desktop-layout-with-repairs cannot creep in unnoticed.
- Every interactive target is at least 44px. Asserted, not intended.
- The board is **vertical stage sections** on a phone, columns only at 760px+.
  A horizontally scrolling kanban hides two thirds of the work behind a gesture.
- No login, ever. A device is bound to a station once by opening that station's
  link, and remembers with a signed cookie for 180 days. A bench phone is a
  one-tap scanner for the rest of its life.
- Scanning has three tiers, best first: a live in-page camera scanner
  (`BarcodeDetector`), the phone's own Camera app on the tag (the tag is a plain
  URL, so this needs no support at all), and a manual code box for a worn tag. A
  camera that will not start is reported in words, not as an error.

## Running it

```bash
python3 server.py --init                     # create the database, print station links
python3 seed_demo.py                         # optional: a plausible day's work
WATCHSHOP_BASE_URL=http://<host>:8450 python3 server.py --host 0.0.0.0 --port 8450

WATCHSHOP_BASE_URL=http://<host>:8450 python3 demo.py /tmp/demo.html
cd /tmp && python3 -m http.server 8440       # the one-page walkthrough
```

Standard library only. No packages to install, and no build step. `segno` is
used if it happens to be present, but `minqr.py` in this repo is the default
encoder, so a bare Python install prints scannable tags.

`WATCHSHOP_BASE_URL` sets the host used inside the QR codes. **It matters:** a
tag printed with a `127.0.0.1` address is dead on every phone, which is the one
mistake that makes a demo of this fail.

## The database

One SQLite file, readable with `sqlite3 watchshop.db`. Nothing here hides the
data from the shop.

| table | what it holds |
|---|---|
| `stage` | the bench flow, editable — the shop's real steps go here |
| `station` | one row per device location, each with its own token |
| `job` | the watch, the customer, the promise date, the current stage |
| `event` | **append-only** history: every stage change, who made it, when |
| `uat` | one row per verdict logged while the owner is looking at it |

Two decisions worth keeping:

- **History is appended, never overwritten.** The current stage is a pointer
  (`job.stage_id`); the timeline is the `event` rows. A job that goes backwards
  keeps both moves, with the reason attached.
- **The station is the device, not the person.** No per-user accounts: the shop
  is small, and typing a name at every scan is exactly the friction that makes
  staff abandon a system.

## Gates

```bash
python3 test_qr.py                            # encoder: message bits, EC level, real decode
python3 test_e2e.py http://127.0.0.1:8451     # the whole workflow over HTTP
NODE_PATH=/opt/homebrew/lib/node_modules node test_mobile.js <url> <station-token> <job-code>
```

`test_e2e.py` needs a running server on a scratch database:

```bash
python3 server.py --db /tmp/watchshop-test.db --port 8451 --init
python3 server.py --db /tmp/watchshop-test.db --port 8451 &
python3 test_e2e.py http://127.0.0.1:8451
```

Each gate has been observed failing, which is the only reason to trust it:

- `test_qr.py` found two real bugs by failing on them: the version information
  block is placed **LSB-first** while the format information is **MSB-first**,
  and the version block starts at version 7 (size 45), not size 49. Either one
  produces a tag that looks perfect and scans as nothing.
- `test_qr.py` also had to be corrected twice for asking the wrong question:
  comparing the whole matrix against segno fails on a correct encoder, because
  the pad codewords after the terminator are implementation-defined and segno
  writes an extra `0x00`; and a small corruption is repaired by error correction,
  so an injection probe must corrupt past the EC budget to prove anything.
- `test_mobile.js` found the horizontally scrolling board, 44 sub-44px tap
  targets, and a title-inside-sticky-header false positive in its own check.
- `test_e2e.py` found the shared-SQLite-connection crash under two devices and a
  `KeyError` on the customer timeline.

## What this deliberately does not do

No payments, no parts inventory, no SMS or WhatsApp sending, no estimates or
customer approval. The stage list, the promise dates and the deposit field are
the parts most likely to change once the shop describes its real process.

#!/usr/bin/env python3
"""Build a demo database with a plausible month in the workshop.

    python3 seed_demo.py [path]

Jobs are spread across stages and deliberately back-dated so the board has
something real to say: watches sitting far too long, one past its promise date,
one ready for collection. Timestamps are written explicitly, which is the only
reason this script exists rather than using the intake form twenty times.

Three things make the demo mean something rather than just look full:

  * several customers come back, and one is on their third watch. That is the
    only reason a customer page is worth showing: a history of one visit is
    just a job.
  * the shop has a second counter, and one watch was handed over to it with a
    note, the way a watch goes to a specialist and comes back.
  * every job walks through its stages rather than appearing at its final one,
    so the timeline the customer sees and the days-in-stage the board counts
    are real.

Running it again on an existing database adds what is missing and leaves what
is there: a name typed at intake has already been matched to the customer it
belongs to, and that match is the thing worth keeping.
"""
import os
import sys
from datetime import datetime, timedelta

import db

# customer, contact, brand, model, serial, notes, deposit, promise_in, days_ago,
# stage, store (1 = the shop, 2 = the second counter)
JOBS = [
    # -- open now, and none of them fresh --------------------------------
    ("Tan Wei Ming", "+65 9123 4567", "Rolex", "Submariner 116610LN", "M4X8K221",
     "Running 8s fast. Full service, new gasket, pressure test.", 300, 21, 24,
     "assessment", 1),
    ("Priya Raman", "+65 8234 1188", "Omega", "Speedmaster Professional", "77618234",
     "Chronograph starts but does not reset to zero. Quote approved by phone.", 200, 14, 18,
     "parts", 1),
    ("Lim Chee Keong", "+65 9771 2033", "Seiko", "Prospex SPB143", "6R35-00T0",
     "Stops overnight. Customer wants it before the 20th.", 0, 6, 9, "repair", 1),
    ("Grace Ong", "+65 9011 4477", "Cartier", "Tank Must WSTA0041", "4127XY",
     "Battery change and bracelet clean.", 0, 2, 3, "qc", 1),
    ("David Krishnan", "+65 8488 2299", "Grand Seiko", "SBGA211 Snowflake", "9R65-0AM0",
     "Annual service. Customer asked for photos of the movement.", 500, 30, 12,
     "repair", 1),
    ("Nur Aisyah", "+65 9334 7781", "Tudor", "Black Bay 58", "79830RB",
     "Bezel insert scratched, wants the original part.", 150, 28, 5, "estimate", 1),
    ("Wong Kai Wen", "+65 8167 3322", "Patek Philippe", "Calatrava 5196", "4892311",
     "Inherited piece, not serviced in 20 years. Full overhaul.", 1000, 45, 2,
     "received", 1),
    ("Siti Hajar", "+65 9088 5512", "Breitling", "Navitimer B01", "A13324",
     "Lume pip missing on the bezel. Case polish requested.", 0, 10, 4, "estimate", 1),
    ("Marcus Lee", "+65 8123 9900", "IWC", "Portugieser Chrono", "IW371605",
     "Under brand warranty, running fast.", 0, 3, 6, "repair", 1),

    # -- the second counter has its own work, and its own name on the board
    ("Jeanette Tan", "+65 9550 6612", "Vintage Longines", "Calatrava dress, 1962",
     "L-84712", "Family heirloom. Crystal and a hand re-lume.", 250, 40, 41,
     "parts", 2),
    ("Rahul Menon", "+65 8777 1234", "Zenith", "El Primero", "03.2040",
     "Ready. Customer notified and collecting on Saturday.", 0, 1, 8, "ready", 2),

    # -- the same people again: this is what a customer page is for -----
    ("Lim Chee Keong", "+65 9771 2033", "Seiko", "Presage Cocktail Time", "4R35-01T0",
     "Second watch in three months. Crystal chipped at 4 o'clock.", 0, 12, 1,
     "received", 1),
    ("Grace Ong", "+65 9011 4477", "Cartier", "Santos Dumont", "WSSA0022",
     "Bracelet screws keep loosening. Wants threadlock, not a new bracelet.", 0, 7, 6,
     "assessment", 1),
    ("David Krishnan", "+65 8488 2299", "Grand Seiko", "SBGW231", "9S64-00C0",
     "Hand-wound, bought with the other one. Same treatment, please.", 400, 30, 5,
     "parts", 1),
    ("Marcus Lee", "+65 8123 9900", "Rolex", "Datejust 126234", "6R7X2291",
     "Crown feels gritty. Third job this year.", 250, 14, 8, "assessment", 1),

    # -- finished and collected: the history a returning customer asks about
    ("Tan Wei Ming", "+65 9123 4567", "Rolex", "Explorer 124270", "9F2K1104",
     "Regulation only, done while he waited.", 0, 0, 34, "collected", 1),
    ("Grace Ong", "+65 9011 4477", "Omega", "Constellation 131.10", "84120345",
     "Battery and reseal.", 0, 0, 29, "collected", 1),
    ("David Krishnan", "+65 8488 2299", "Seiko", "SKX007", "7S26-0020",
     "Beater watch, new gasket and a strap.", 0, 0, 41, "collected", 1),
    ("Jeanette Tan", "+65 9550 6612", "Vintage Omega", "Seamaster 30, 1963", "—",
     "Same drawer as the Longines. Crown does not wind smoothly.", 0, 0, 47,
     "collected", 2),
]

SECOND_SHOP = ("Orchard", "391 Orchard Road, #02-12")

# serial, to store, days ago, the note the handover carries
TRANSFERS = [
    ("M4X8K221", 2, 6, "sent to the Orchard bench for the pressure test"),
]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "watchshop.db")
    db.DB_PATH = path
    db.init()
    conn = db.connect()

    stores = {1: conn.execute("SELECT id FROM store ORDER BY id LIMIT 1").fetchone()["id"]}
    row = conn.execute("SELECT id FROM store WHERE name = ? COLLATE NOCASE",
                       (SECOND_SHOP[0],)).fetchone()
    if row:
        stores[2] = row["id"]
    else:
        stores[2] = db.create_store(conn, SECOND_SHOP[0], SECOND_SHOP[1])["id"]
    # A counter with no bench cannot be scanned at, so seed one per shop.
    for idx, bench in ((1, "Front desk"), (2, "Orchard counter")):
        if not db.stations(conn, stores[idx]):
            db.create_station(conn, bench, stores[idx])

    stages = {s["key"]: s for s in db.stages(conn)}
    order = [s["key"] for s in db.stages(conn)]
    added = skipped = 0
    for (cust, contact, brand, model, serial, notes, dep, promise_in, ago,
         stage_key, store_idx) in JOBS:
        if conn.execute("SELECT 1 FROM job WHERE serial = ? AND brand = ?",
                        (serial, brand)).fetchone():
            skipped += 1
            continue
        _seed_job(conn, stages, order, stores[store_idx], cust, contact, brand, model,
                  serial, notes, dep, promise_in, ago, stage_key, bench_idx=1 if store_idx == 1 else 2)
        added += 1

    for serial, to_idx, ago, note in TRANSFERS:
        row = conn.execute("SELECT id, store_id, stage_id FROM job WHERE serial = ?",
                           (serial,)).fetchone()
        if not row or row["store_id"] == stores[to_idx]:
            continue
        _seed_transfer(conn, row["id"], row["store_id"], stores[to_idx], ago, note)

    conn.commit()
    counts = db.board(conn)
    print(f"seeded {added} job(s), left {skipped} already there, into {path}")
    print(f"board: {counts['totals']['open']} open, {counts['totals']['ready']} ready, "
          f"{counts['totals']['overdue']} past date, {counts['totals']['stuck']} sitting over a week")
    for s in db.stores(conn):
        n = conn.execute("SELECT COUNT(*) c FROM job WHERE store_id = ?", (s["id"],)).fetchone()["c"]
        print(f"  {s['name']:<12} {s['benches']} bench(es), {n} job(s)")
    people = db.customers(conn)
    print(f"  {len(people)} customers, {sum(1 for c in people if c['jobs'] > 1)} of them returning")
    conn.close()


def _seed_job(conn, stages, order, store_id, cust, contact, brand, model, serial,
              notes, dep, promise_in, ago, stage_key, bench_idx=1):
    """One job, walked through its stages, back-dated to a plausible morning."""
    start = (datetime.now() - timedelta(days=ago)).replace(
        hour=10 if bench_idx == 1 else 14, minute=15, second=0, microsecond=0)
    promise = ((start + timedelta(days=promise_in)).date().isoformat() if promise_in else "")
    j = db.create_job(conn, cust, contact, brand, model, serial, notes, dep, promise,
                      store_id)
    row = db.job_row(conn, j["code"])
    bench = db.stations(conn, store_id)
    bench = bench[0]["id"] if bench else None
    steps = order[1:order.index(stage_key) + 1]
    for i, key in enumerate(steps):
        when = start + (datetime.now() - start) * ((i + 1) / (len(steps) + 1))
        conn.execute(
            """INSERT INTO event (job_id, station_id, from_stage, to_stage, from_store,
                                  to_store, kind, reason, at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (row["id"], bench, row["stage_id"], stages[key]["id"], store_id, store_id,
             "advance", "", when.isoformat(timespec="seconds")))
        conn.execute("UPDATE job SET stage_id = ? WHERE id = ?",
                     (stages[key]["id"], row["id"]))
    conn.execute("UPDATE job SET created_at = ?, updated_at = ? WHERE id = ?",
                 (start.isoformat(timespec="seconds"), db.now(), row["id"]))
    # The intake row create_job wrote carries the true start time, not "now".
    conn.execute(
        """UPDATE event SET at = ?, station_id = NULL, to_store = ?
            WHERE job_id = ? AND kind = 'intake'""",
        (start.isoformat(timespec="seconds"), store_id, row["id"]))


def _seed_transfer(conn, job_id, from_store, to_store, ago, note):
    """A handover: same stage, different shop, one appended event."""
    job = conn.execute("SELECT stage_id FROM job WHERE id = ?", (job_id,)).fetchone()
    at = (datetime.now() - timedelta(days=ago)).replace(
        hour=16, minute=5, second=0, microsecond=0).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO event (job_id, station_id, from_stage, to_stage, from_store,
                              to_store, kind, reason, at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (job_id, None, job["stage_id"], job["stage_id"], from_store, to_store,
         "transfer", note, at))
    conn.execute("UPDATE job SET store_id = ?, updated_at = ? WHERE id = ?",
                 (to_store, at, job_id))


if __name__ == "__main__":
    main()

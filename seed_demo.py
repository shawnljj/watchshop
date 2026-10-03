#!/usr/bin/env python3
"""Build a demo database with a plausible day in the workshop.

    python3 seed_demo.py [path]

Jobs are spread across stages and deliberately back-dated so the board has
something real to say: two watches sitting far too long, one past its promise
date, one ready for collection. Timestamps are written explicitly, which is the
only reason this script exists rather than using the intake form eleven times.
"""
import os
import sys
from datetime import datetime, timedelta

import db

JOBS = [
    # customer, contact, brand, model, serial, notes, deposit, promise_days, days_ago, stage
    ("Tan Wei Ming", "+65 9123 4567", "Rolex", "Submariner 116610LN", "M4X8K221",
     "Running 8s fast. Full service, new gasket, pressure test.", 300, 21, 24, "assessment"),
    ("Priya Raman", "+65 8234 1188", "Omega", "Speedmaster Professional", "77618234",
     "Chronograph starts but does not reset to zero. Service quote approved by phone.",
     200, 14, 18, "parts"),
    ("Lim Chee Keong", "+65 9771 2033", "Seiko", "Prospex SPB143", "6R35-00T0",
     "Stops overnight. Customer wants it before the 20th.", 0, 6, 9, "repair"),
    ("Grace Ong", "+65 9011 4477", "Cartier", "Tank Must WSTA0041", "4127XY",
     "Battery change and bracelet clean.", 0, 2, 3, "qc"),
    ("David Krishnan", "+65 8488 2299", "Grand Seiko", "SBGA211 Snowflake",
     "9R65-0AM0", "Annual service. Customer asked for photos of the movement.",
     500, 30, 12, "repair"),
    ("Nur Aisyah", "+65 9334 7781", "Tudor", "Black Bay 58", "79830RB",
     "Bezel insert scratched, wants original part.", 150, 28, 5, "estimate"),
    ("Wong Kai Wen", "+65 8167 3322", "Patek Philippe", "Calatrava 5196",
     "4892311", "Inherited piece, has not been serviced in 20 years. Full overhaul.",
     1000, 45, 2, "received"),
    ("Siti Hajar", "+65 9088 5512", "Breitling", "Navitimer B01", "A13324",
     "Lume pip missing on the bezel. Case polish requested.", 0, 10, 4, "estimate"),
    ("Marcus Lee", "+65 8123 9900", "IWC", "Portugieser Chrono", "IW371605",
     "Under brand warranty, running fast.", 0, 3, 6, "repair"),
    ("Jeanette Tan", "+65 9550 6612", "Vintage Longines", "Calatrava dress, 1962",
     "L-84712", "Family heirloom. Crystal replacement and hand re-lume.", 250, 40, 41, "parts"),
    ("Rahul Menon", "+65 8777 1234", "Zenith", "El Primero", "03.2040",
     "Ready. Customer notified and collecting Saturday.", 0, 1, 8, "ready"),
    ("Amelia Chua", "+65 9345 6677", "Jaeger-LeCoultre", "Reverso Classic",
     "Q2548520", "Bracelet sized, minor scratches to the case back.", 0, 4, 15, "repair"),
]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "watchshop.db")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    db.DB_PATH = path
    db.init()
    conn = db.connect()
    stages = {s["key"]: s for s in db.stages(conn)}
    stations = db.stations(conn)
    today = datetime.now()

    for (cust, contact, brand, model, serial, notes, dep, promise_days,
         ago, stage_key) in JOBS:
        start = today - timedelta(days=ago)
        j = db.create_job(conn, cust, contact, brand, model, serial, notes, dep,
                          (today + timedelta(days=promise_days)).date().isoformat())
        row = db.job_row(conn, j["code"])
        order = [s["key"] for s in db.stages(conn)]
        steps = order[1:order.index(stage_key) + 1]
        # spread the moves between intake and now, so "days in stage" is real
        for i, key in enumerate(steps):
            when = start + (today - start) * ((i + 1) / (len(steps) + 1))
            conn.execute(
                """INSERT INTO event (job_id, station_id, from_stage, to_stage, kind, reason, at)
                   VALUES (?,?,?,?,?,?,?)""",
                (row["id"], stations[1]["id"], row["stage_id"], stages[key]["id"],
                 "advance", "", when.isoformat(timespec="seconds")))
            conn.execute("UPDATE job SET stage_id = ? WHERE id = ?",
                         (stages[key]["id"], row["id"]))
        conn.execute(
            "UPDATE job SET created_at = ?, updated_at = ? WHERE id = ?",
            (start.isoformat(timespec="seconds"), db.now(), row["id"]))
        conn.execute("UPDATE event SET at = ? WHERE job_id = ? AND kind = 'intake'",
                     (start.isoformat(timespec="seconds"), row["id"]))
    conn.commit()
    n = conn.execute("SELECT COUNT(*) c FROM job").fetchone()["c"]
    print(f"seeded {n} jobs into {path}")
    print(f"board: {n} open, "
          f"{conn.execute('SELECT COUNT(*) c FROM event').fetchone()['c']} events")
    conn.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""SQLite layer for the watch workshop tracker.

Standard library only. One file is the whole database, and it is meant to be
read with `sqlite3 watchshop.db`.

Nothing here is demo data the server pretends not to have: the stages are real
rows in a `stage` table that the shop can edit, and every stage change is an
appended `event` row rather than an overwrite, so the timeline on the customer
page and the timings on the board come from the same records.
"""
import os
import secrets
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("WATCHSHOP_DB", os.path.join(ROOT, "watchshop.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS stage (
    id         INTEGER PRIMARY KEY,
    key        TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    position   INTEGER NOT NULL UNIQUE,
    is_closed  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS station (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    token      TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job (
    id         INTEGER PRIMARY KEY,
    code       TEXT NOT NULL UNIQUE,
    customer   TEXT NOT NULL,
    contact    TEXT NOT NULL DEFAULT '',
    brand      TEXT NOT NULL DEFAULT '',
    model      TEXT NOT NULL DEFAULT '',
    serial     TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    deposit    INTEGER NOT NULL DEFAULT 0,
    promise    TEXT NOT NULL DEFAULT '',
    stage_id   INTEGER NOT NULL REFERENCES stage(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event (
    id          INTEGER PRIMARY KEY,
    job_id      INTEGER NOT NULL REFERENCES job(id),
    station_id  INTEGER REFERENCES station(id),
    from_stage  INTEGER REFERENCES stage(id),
    to_stage    INTEGER NOT NULL REFERENCES stage(id),
    kind        TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS uat (
    id       INTEGER PRIMARY KEY,
    job_code TEXT NOT NULL,
    verdict  TEXT NOT NULL,
    note     TEXT NOT NULL DEFAULT '',
    at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_job_code ON job(code);
CREATE INDEX IF NOT EXISTS ix_event_job ON event(job_id, id);
CREATE INDEX IF NOT EXISTS ix_job_stage ON job(stage_id);
"""

# The default bench flow. Seeded once; edit these rows to match the shop.
# The friend's real stage list replaces this before anything is shipped.
DEFAULT_STAGES = [
    ("received",   "Received at counter", 1, 0),
    ("assessment", "On the bench",        2, 0),
    ("estimate",   "Estimate sent",       3, 0),
    ("parts",      "Waiting for parts",   4, 0),
    ("repair",     "In repair",           5, 0),
    ("qc",         "Final check",         6, 0),
    ("ready",      "Ready for collection", 7, 1),
    ("collected",  "Collected",           8, 1),
]

DEFAULT_STATIONS = ["Intake counter", "Bench 1", "Bench 2", "Front desk"]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def new_token() -> str:
    return secrets.token_hex(12)


def new_code() -> str:
    # 10 chars of base32, no ambiguous 0/1/I/O. Unguessable and readable aloud.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


def init(path: str = None) -> None:
    """Create the schema and seed stages/stations if the file is new.

    `path` is a convenience for --init; the server always uses DB_PATH.
    """
    target = path or DB_PATH
    fresh = not os.path.exists(target)
    conn = connect()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) c FROM stage").fetchone()["c"] == 0:
        conn.executemany(
            "INSERT INTO stage (key, name, position, is_closed) VALUES (?,?,?,?)",
            DEFAULT_STAGES)
    if conn.execute("SELECT COUNT(*) c FROM station").fetchone()["c"] == 0:
        conn.executemany(
            "INSERT INTO station (name, token, created_at) VALUES (?,?,?)",
            [(n, new_token(), now()) for n in DEFAULT_STATIONS])
    conn.commit()
    if fresh:
        for s in conn.execute("SELECT name, token FROM station ORDER BY id"):
            print(f"  station {s['name']:<16} /s/{s['token']}")
    conn.close()


# ---------------------------------------------------------------- reads

def stages(conn) -> list:
    return [dict(r) for r in conn.execute("SELECT * FROM stage ORDER BY position")]


def stage_by_key(conn, key: str):
    return conn.execute("SELECT * FROM stage WHERE key = ?", (key,)).fetchone()


def station_by_token(conn, token: str):
    return conn.execute("SELECT * FROM station WHERE token = ?", (token,)).fetchone()


def stations(conn) -> list:
    return [dict(r) for r in conn.execute("SELECT * FROM station ORDER BY id")]


def job_row(conn, code: str):
    return conn.execute("SELECT * FROM job WHERE code = ?", (code.upper(),)).fetchone()


def job_events(conn, job_id: int) -> list:
    return [dict(r) for r in conn.execute(
        """SELECT e.*, s.name AS to_name, f.name AS from_name, st.name AS station_name
             FROM event e
             JOIN stage s  ON s.id = e.to_stage
             LEFT JOIN stage f  ON f.id = e.from_stage
             LEFT JOIN station st ON st.id = e.station_id
            WHERE e.job_id = ? ORDER BY e.id""", (job_id,))]


def board(conn) -> dict:
    """Every open job, grouped by stage, oldest in stage first."""
    rows = conn.execute(
        """SELECT j.*, s.name AS stage_name, s.position AS stage_position,
                  s.is_closed, e.at AS stage_since,
                  CAST(julianday('now') - julianday(e.at) AS INTEGER) AS days_in_stage
             FROM job j
             JOIN stage s ON s.id = j.stage_id
             LEFT JOIN event e ON e.id = (SELECT MAX(id) FROM event WHERE job_id = j.id)
            ORDER BY s.position, stage_since""").fetchall()
    cols = [{"stage": s["name"], "key": s["key"], "position": s["position"],
             "closed": bool(s["is_closed"]), "jobs": []} for s in stages(conn)]
    by_key = {c["key"]: c for c in cols}
    for r in rows:
        d = dict(r)
        d["overdue"] = bool(d["promise"]) and d["promise"] < datetime.now().date().isoformat()
        by_key[conn.execute("SELECT key FROM stage WHERE id=?", (d["stage_id"],)).fetchone()["key"]]["jobs"].append(d)
    open_jobs = [d for c in cols for d in c["jobs"]]
    ready = next((c for c in cols if c["key"] == "ready"), None)
    return {
        "columns": cols,
        "totals": {
            "open": sum(1 for d in open_jobs if not d["is_closed"]),
            "ready": len(ready["jobs"]) if ready else 0,
            "overdue": sum(1 for d in open_jobs if d["overdue"]),
            "stuck": sum(1 for d in open_jobs if d["days_in_stage"] >= 7 and not d["is_closed"]),
        },
        "stuck": [d for d in open_jobs if d["days_in_stage"] >= 7 and not d["is_closed"]],
    }


def job_view(conn, code: str) -> dict:
    row = job_row(conn, code)
    if not row:
        return {}
    j = dict(row)
    all_stages = stages(conn)
    evs = job_events(conn, j["id"])
    reached = []
    for e in evs:
        reached.append({"stage": e["to_name"], "to_name": e["to_name"],
                        "at": e["at"], "kind": e["kind"],
                        "station": e["station_name"] or "", "reason": e["reason"]})
    current = next(s for s in all_stages if s["id"] == j["stage_id"])
    nxt = next((s for s in all_stages if s["position"] == current["position"] + 1), None)
    return {
        "code": j["code"], "customer": j["customer"], "contact": j["contact"],
        "brand": j["brand"], "model": j["model"], "serial": j["serial"],
        "notes": j["notes"], "deposit": j["deposit"], "promise": j["promise"],
        "created_at": j["created_at"], "updated_at": j["updated_at"],
        "stage": {"key": current["key"], "name": current["name"],
                  "position": current["position"], "closed": bool(current["is_closed"])},
        "next": ({"key": nxt["key"], "name": nxt["name"]} if nxt else None),
        "stages": [{"key": s["key"], "name": s["name"], "position": s["position"],
                    "state": ("done" if s["position"] <= current["position"] else "ahead")}
                   for s in all_stages],
        "timeline": reached,
        "days_in_stage": max(0, _days(evs[-1]["at"]) if evs else 0),
    }


def _days(iso: str) -> int:
    try:
        return (datetime.now() - datetime.fromisoformat(iso)).days
    except ValueError:
        return 0


# --------------------------------------------------------------- writes

def create_job(conn, customer, contact, brand, model, serial, notes, deposit,
               promise) -> dict:
    first = conn.execute("SELECT * FROM stage ORDER BY position LIMIT 1").fetchone()
    code = new_code()
    while job_row(conn, code):
        code = new_code()
    ts = now()
    cur = conn.execute(
        """INSERT INTO job (code, customer, contact, brand, model, serial, notes,
                            deposit, promise, stage_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (code, customer, contact, brand, model, serial, notes, deposit, promise,
         first["id"], ts, ts))
    conn.execute(
        """INSERT INTO event (job_id, station_id, from_stage, to_stage, kind, reason, at)
           VALUES (?,?,?,?,?,?,?)""",
        (cur.lastrowid, None, None, first["id"], "intake", "", ts))
    conn.commit()
    return job_view(conn, code)


def move(conn, code, station_id, to_key=None, back=False, reason="") -> dict:
    """Advance one stage, or step back one with a reason. Returns None if refused."""
    j = job_row(conn, code)
    if not j:
        return {"error": "no such job"}
    all_stages = stages(conn)
    current = next(s for s in all_stages if s["id"] == j["stage_id"])
    if back:
        if not reason.strip():
            return {"error": "stepping back needs a reason"}
        target = next((s for s in all_stages if s["position"] == current["position"] - 1), None)
        kind = "back"
    else:
        target = next((s for s in all_stages if s["position"] == current["position"] + 1), None)
        kind = "advance"
        if current["is_closed"]:
            return {"error": "this job is already closed"}
    if not target:
        return {"error": "no further stage"}
    ts = now()
    conn.execute(
        """INSERT INTO event (job_id, station_id, from_stage, to_stage, kind, reason, at)
           VALUES (?,?,?,?,?,?,?)""",
        (j["id"], station_id, current["id"], target["id"], kind, reason.strip(), ts))
    conn.execute("UPDATE job SET stage_id = ?, updated_at = ? WHERE id = ?",
                 (target["id"], ts, j["id"]))
    conn.commit()
    return job_view(conn, code)


def log_uat(conn, job_code, verdict, note) -> None:
    conn.execute("INSERT INTO uat (job_code, verdict, note, at) VALUES (?,?,?,?)",
                 (job_code, verdict, note, now()))
    conn.commit()

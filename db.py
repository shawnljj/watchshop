#!/usr/bin/env python3
"""SQLite layer for the watch workshop tracker.

Standard library only. One file is the whole database, and it is meant to be
read with `sqlite3 watchshop.db`.

Nothing here is demo data the server pretends not to have: the stages and the
shops are real rows the shop can edit, and every stage change is an appended
`event` row rather than an overwrite, so the timeline on the customer page and
the timings on the board come from the same records.

Four nouns, and the difference between them matters:

  store     a shop or a counter. One row per place work happens.
  station   a bench, a desk, a phone. Belongs to a store; the device, not the
            person, and it is what a device is bound to.
  customer  a person. Jobs point at a customer row, not at a typed name, which
            is what makes a history possible.
  job       one watch, at one stage, in the hands of one store.
"""
import os
import secrets
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("WATCHSHOP_DB", os.path.join(ROOT, "watchshop.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS store (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    address    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customer (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    contact    TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

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
    store_id   INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job (
    id          INTEGER PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    customer    TEXT NOT NULL,
    customer_id INTEGER,
    contact     TEXT NOT NULL DEFAULT '',
    brand       TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    serial      TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    deposit     INTEGER NOT NULL DEFAULT 0,
    promise     TEXT NOT NULL DEFAULT '',
    store_id    INTEGER,
    stage_id    INTEGER NOT NULL REFERENCES stage(id),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event (
    id          INTEGER PRIMARY KEY,
    job_id      INTEGER NOT NULL REFERENCES job(id),
    station_id  INTEGER REFERENCES station(id),
    from_stage  INTEGER REFERENCES stage(id),
    to_stage    INTEGER NOT NULL REFERENCES stage(id),
    from_store  INTEGER,
    to_store    INTEGER,
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

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_job_store ON job(store_id);
CREATE INDEX IF NOT EXISTS ix_job_customer ON job(customer_id);
CREATE INDEX IF NOT EXISTS ix_station_store ON station(store_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_customer_name ON customer(name COLLATE NOCASE);
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

DEFAULT_STORE = "The shop"
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


def _columns(conn, table) -> set:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn) -> None:
    """Bring a database made before shops and customers existed up to date.

    The live demo database is workshop data, not source, so it cannot simply be
    deleted: a job registered at the counter yesterday has to survive the
    change. Everything backfills to one implied shop, and every name typed
    into `job.customer` becomes a customer row.
    """
    if _columns(conn, "station") and "store_id" not in _columns(conn, "station"):
        conn.execute("ALTER TABLE station ADD COLUMN store_id INTEGER")
    job_cols = _columns(conn, "job")
    if job_cols and "store_id" not in job_cols:
        conn.execute("ALTER TABLE job ADD COLUMN store_id INTEGER")
    if job_cols and "customer_id" not in job_cols:
        conn.execute("ALTER TABLE job ADD COLUMN customer_id INTEGER")
    event_cols = _columns(conn, "event")
    if event_cols and "from_store" not in event_cols:
        conn.execute("ALTER TABLE event ADD COLUMN from_store INTEGER")
    if event_cols and "to_store" not in event_cols:
        conn.execute("ALTER TABLE event ADD COLUMN to_store INTEGER")

    if conn.execute("SELECT COUNT(*) c FROM store").fetchone()["c"] == 0:
        conn.execute("INSERT INTO store (name, address, created_at) VALUES (?,?,?)",
                     (DEFAULT_STORE, "", now()))
    default_store = conn.execute("SELECT id FROM store ORDER BY id LIMIT 1").fetchone()["id"]
    conn.execute("UPDATE station SET store_id = ? WHERE store_id IS NULL", (default_store,))
    conn.execute("UPDATE job SET store_id = ? WHERE store_id IS NULL", (default_store,))
    for r in conn.execute("SELECT DISTINCT customer, contact FROM job ORDER BY id").fetchall():
        if not r["customer"].strip():
            continue
        conn.execute(
            """INSERT OR IGNORE INTO customer (name, contact, notes, created_at)
               VALUES (?,?,?,?)""", (r["customer"].strip(), r["contact"] or "", "", now()))
    conn.execute(
        """UPDATE job SET customer_id =
              (SELECT id FROM customer WHERE customer.name = job.customer COLLATE NOCASE)
            WHERE customer_id IS NULL""")


def init(path: str = None) -> None:
    """Create the schema, migrate an older one, seed stages/stations if empty.

    `path` is a convenience for --init; the server always uses DB_PATH.
    """
    target = path or DB_PATH
    fresh = not os.path.exists(target)
    conn = connect()
    conn.executescript(SCHEMA)
    _migrate(conn)
    # Indexes are created last, on purpose: executescript runs before the
    # migration, and an index on a column an older database has not got yet
    # would abort the whole schema before the migration could add it.
    conn.executescript(INDEXES)
    if conn.execute("SELECT COUNT(*) c FROM stage").fetchone()["c"] == 0:
        conn.executemany(
            "INSERT INTO stage (key, name, position, is_closed) VALUES (?,?,?,?)",
            DEFAULT_STAGES)
    if conn.execute("SELECT COUNT(*) c FROM station").fetchone()["c"] == 0:
        store = conn.execute("SELECT id FROM store ORDER BY id LIMIT 1").fetchone()["id"]
        conn.executemany(
            "INSERT INTO station (name, token, store_id, created_at) VALUES (?,?,?,?)",
            [(n, new_token(), store, now()) for n in DEFAULT_STATIONS])
    conn.commit()
    if fresh:
        print(f"  store {DEFAULT_STORE}")
        for s in conn.execute("SELECT name, token FROM station ORDER BY id"):
            print(f"  station {s['name']:<16} /s/{s['token']}")
    conn.close()


# ---------------------------------------------------------------- reads

def stages(conn) -> list:
    return [dict(r) for r in conn.execute("SELECT * FROM stage ORDER BY position")]


def stage_by_key(conn, key: str):
    return conn.execute("SELECT * FROM stage WHERE key = ?", (key,)).fetchone()


def stations(conn, store_id=None) -> list:
    if store_id is None:
        rows = conn.execute(
            """SELECT st.*, s.name AS store_name FROM station st
               LEFT JOIN store s ON s.id = st.store_id ORDER BY st.id""")
    else:
        rows = conn.execute(
            """SELECT st.*, s.name AS store_name FROM station st
               LEFT JOIN store s ON s.id = st.store_id
              WHERE st.store_id = ? ORDER BY st.id""", (store_id,))
    return [dict(r) for r in rows]


def station_by_token(conn, token: str):
    return conn.execute(
        """SELECT st.*, s.name AS store_name FROM station st
           LEFT JOIN store s ON s.id = st.store_id WHERE st.token = ?""",
        (token,)).fetchone()


def station_by_id(conn, sid):
    return conn.execute(
        """SELECT st.*, s.name AS store_name FROM station st
           LEFT JOIN store s ON s.id = st.store_id WHERE st.id = ?""", (sid,)).fetchone()


def stores(conn) -> list:
    """Every shop, with the two numbers the board needs to make sense of it."""
    return [dict(r) for r in conn.execute(
        """SELECT s.*,
                  (SELECT COUNT(*) FROM station WHERE store_id = s.id) AS benches,
                  (SELECT COUNT(*) FROM job j JOIN stage g ON g.id = j.stage_id
                    WHERE j.store_id = s.id AND g.is_closed = 0) AS open_jobs,
                  (SELECT COUNT(*) FROM job j WHERE j.store_id = s.id) AS all_jobs
             FROM store s ORDER BY s.id""")]


def store_by_id(conn, sid):
    return conn.execute("SELECT * FROM store WHERE id = ?", (sid,)).fetchone()


def job_row(conn, code: str):
    return conn.execute("SELECT * FROM job WHERE code = ?", (code.upper(),)).fetchone()


def job_events(conn, job_id: int) -> list:
    return [dict(r) for r in conn.execute(
        """SELECT e.*, s.name AS to_name, f.name AS from_name, st.name AS station_name,
                  ds.name AS from_store_name, ts.name AS to_store_name
             FROM event e
             JOIN stage s  ON s.id = e.to_stage
             LEFT JOIN stage f  ON f.id = e.from_stage
             LEFT JOIN station st ON st.id = e.station_id
             LEFT JOIN store ds ON ds.id = e.from_store
             LEFT JOIN store ts ON ts.id = e.to_store
            WHERE e.job_id = ? ORDER BY e.id""", (job_id,))]


def board(conn, store_id=None) -> dict:
    """Every job, grouped by stage, oldest in stage first.

    `store_id` narrows the whole board to one shop; None is the whole business.
    """
    where, params = "", []
    if store_id is not None:
        where, params = "WHERE j.store_id = ?", [store_id]
    rows = conn.execute(
        f"""SELECT j.*, s.name AS stage_name, s.position AS stage_position,
                  s.is_closed, e.at AS stage_since, sh.name AS store_name,
                  CAST(julianday('now') - julianday(e.at) AS INTEGER) AS days_in_stage
             FROM job j
             JOIN stage s ON s.id = j.stage_id
             LEFT JOIN store sh ON sh.id = j.store_id
             LEFT JOIN event e ON e.id = (SELECT MAX(id) FROM event WHERE job_id = j.id)
             {where}
            ORDER BY s.position, stage_since""", params).fetchall()
    stage_keys = {s["id"]: s["key"] for s in stages(conn)}
    cols = [{"stage": s["name"], "key": s["key"], "position": s["position"],
             "closed": bool(s["is_closed"]), "jobs": []} for s in stages(conn)]
    by_key = {c["key"]: c for c in cols}
    for r in rows:
        d = dict(r)
        d["overdue"] = bool(d["promise"]) and d["promise"] < datetime.now().date().isoformat()
        by_key[stage_keys[d["stage_id"]]]["jobs"].append(d)
    open_jobs = [d for c in cols for d in c["jobs"]]
    ready = next((c for c in cols if c["key"] == "ready"), None)
    return {
        "columns": cols,
        "store_id": store_id,
        "stores": stores(conn),
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
                        "station": e["station_name"] or "", "reason": e["reason"],
                        "store": (e["to_store_name"] or "" if e["kind"] == "transfer" else "")})
    current = next(s for s in all_stages if s["id"] == j["stage_id"])
    nxt = next((s for s in all_stages if s["position"] == current["position"] + 1), None)
    store = store_by_id(conn, j["store_id"]) if j["store_id"] else None
    return {
        "code": j["code"], "customer": j["customer"], "contact": j["contact"],
        "customer_id": j["customer_id"],
        "brand": j["brand"], "model": j["model"], "serial": j["serial"],
        "notes": j["notes"], "deposit": j["deposit"], "promise": j["promise"],
        "created_at": j["created_at"], "updated_at": j["updated_at"],
        "store_id": j["store_id"],
        "store": ({"id": store["id"], "name": store["name"], "address": store["address"]}
                  if store else None),
        "stage": {"key": current["key"], "name": current["name"],
                  "position": current["position"], "closed": bool(current["is_closed"])},
        "next": ({"key": nxt["key"], "name": nxt["name"]} if nxt else None),
        "stages": [{"key": s["key"], "name": s["name"], "position": s["position"],
                    "state": ("done" if s["position"] <= current["position"] else "ahead")}
                   for s in all_stages],
        "timeline": reached,
        "days_in_stage": max(0, _days(evs[-1]["at"]) if evs else 0),
    }


def customers(conn, q: str = "") -> list:
    """Everyone who has ever handed over a watch, most recent first."""
    q = (q or "").strip()
    like = f"%{q.lower()}%"
    return [dict(r) for r in conn.execute(
        """SELECT c.*,
                  (SELECT COUNT(*) FROM job j WHERE j.customer_id = c.id) AS jobs,
                  (SELECT COUNT(*) FROM job j JOIN stage s ON s.id = j.stage_id
                    WHERE j.customer_id = c.id AND s.is_closed = 0) AS open_jobs,
                  (SELECT MAX(j.created_at) FROM job j WHERE j.customer_id = c.id) AS last_seen
             FROM customer c
            WHERE ? = '' OR lower(c.name) LIKE ? OR c.contact LIKE ?
            ORDER BY (last_seen IS NULL), last_seen DESC, c.name""",
        (q, like, like))]


def customer_view(conn, cid) -> dict:
    row = conn.execute("SELECT * FROM customer WHERE id = ?", (cid,)).fetchone()
    if not row:
        return {}
    c = dict(row)
    jobs = [dict(r) for r in conn.execute(
        """SELECT j.id, j.code, j.brand, j.model, j.serial, j.notes, j.deposit,
                  j.promise, j.created_at, j.updated_at, j.store_id,
                  s.name AS stage_name, s.key AS stage_key, s.is_closed,
                  sh.name AS store_name
             FROM job j JOIN stage s ON s.id = j.stage_id
             LEFT JOIN store sh ON sh.id = j.store_id
            WHERE j.customer_id = ? ORDER BY j.created_at DESC""", (cid,))]
    open_jobs = [j for j in jobs if not j["is_closed"]]
    past = [j for j in jobs if j["is_closed"]]
    return {
        "id": c["id"], "name": c["name"], "contact": c["contact"],
        "notes": c["notes"], "created_at": c["created_at"],
        "jobs": jobs, "open_jobs": open_jobs, "past_jobs": past,
        "totals": {"jobs": len(jobs), "open": len(open_jobs), "past": len(past),
                   "deposit": sum(j["deposit"] or 0 for j in open_jobs)},
        "last_seen": max((j["created_at"] for j in jobs), default=""),
        # The customer's own row is created the day they are first typed into
        # the book, which is not the same day they first walked in: a shop that
        # has been open for years starts its book today. Their earliest job is
        # the honest answer.
        "first_seen": min((j["created_at"] for j in jobs), default=c["created_at"]),
    }


def _days(iso: str) -> int:
    try:
        return (datetime.now() - datetime.fromisoformat(iso)).days
    except ValueError:
        return 0


# --------------------------------------------------------------- writes

def create_store(conn, name, address="") -> dict:
    name = (name or "").strip()
    if not name:
        return {"error": "a shop needs a name"}
    if conn.execute("SELECT 1 FROM store WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
        return {"error": f"there is already a shop called {name}"}
    cur = conn.execute("INSERT INTO store (name, address, created_at) VALUES (?,?,?)",
                       (name, (address or "").strip(), now()))
    conn.commit()
    return dict(store_by_id(conn, cur.lastrowid))


def create_station(conn, name, store_id) -> dict:
    """Add a bench. The token is minted here; the QR is rendered from it."""
    name = (name or "").strip()
    if not name:
        return {"error": "a bench needs a name"}
    if not store_by_id(conn, store_id):
        return {"error": "no such shop"}
    cur = conn.execute(
        "INSERT INTO station (name, token, store_id, created_at) VALUES (?,?,?,?)",
        (name, new_token(), store_id, now()))
    conn.commit()
    return dict(station_by_id(conn, cur.lastrowid))


def find_or_create_customer(conn, name, contact="") -> dict:
    """Match on the name, case-insensitively. A name typed twice is one person.

    A returning customer with a blank phone number gets the new number filled
    in, which is the one piece of upkeep the counter does for free.
    """
    name = (name or "").strip()
    contact = (contact or "").strip()
    if not name:
        return {}
    row = conn.execute("SELECT * FROM customer WHERE name = ? COLLATE NOCASE",
                       (name,)).fetchone()
    if row:
        if contact and not row["contact"]:
            conn.execute("UPDATE customer SET contact = ? WHERE id = ?", (contact, row["id"]))
            conn.commit()
            row = conn.execute("SELECT * FROM customer WHERE id = ?", (row["id"],)).fetchone()
        return dict(row)
    cur = conn.execute("INSERT INTO customer (name, contact, notes, created_at) VALUES (?,?,?,?)",
                       (name, contact, "", now()))
    conn.commit()
    return dict(conn.execute("SELECT * FROM customer WHERE id = ?", (cur.lastrowid,)).fetchone())


def create_job(conn, customer, contact, brand, model, serial, notes, deposit,
               promise, store_id=None) -> dict:
    first = conn.execute("SELECT * FROM stage ORDER BY position LIMIT 1").fetchone()
    cust = find_or_create_customer(conn, customer, contact)
    code = new_code()
    while job_row(conn, code):
        code = new_code()
    ts = now()
    cur = conn.execute(
        """INSERT INTO job (code, customer, customer_id, contact, brand, model, serial,
                            notes, deposit, promise, store_id, stage_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (code, cust.get("name", customer), cust.get("id"), contact, brand, model, serial,
         notes, deposit, promise, store_id, first["id"], ts, ts))
    conn.execute(
        """INSERT INTO event (job_id, station_id, from_stage, to_stage, from_store,
                              to_store, kind, reason, at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (cur.lastrowid, None, None, first["id"], None, store_id, "intake", "", ts))
    conn.commit()
    return job_view(conn, code)


def move(conn, code, station_id, to_key=None, back=False, reason="") -> dict:
    """Advance one stage, or step back one with a reason. Returns None if refused.

    A device at a different shop than the job's is a handover, not a stage: the
    event says so and the job changes hands, so the other shop's board picks it
    up and this one stops counting it.
    """
    j = job_row(conn, code)
    if not j:
        return {"error": "no such job"}
    station = station_by_id(conn, station_id)
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
    to_store = station["store_id"] if station else j["store_id"]
    if kind == "advance" and to_store and j["store_id"] and to_store != j["store_id"]:
        kind = "transfer"
    ts = now()
    conn.execute(
        """INSERT INTO event (job_id, station_id, from_stage, to_stage, from_store,
                              to_store, kind, reason, at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (j["id"], station_id, current["id"], target["id"], j["store_id"],
         to_store if kind == "transfer" else j["store_id"], kind, reason.strip(), ts))
    if kind == "transfer":
        conn.execute("UPDATE job SET store_id = ?, stage_id = ?, updated_at = ? WHERE id = ?",
                     (to_store, target["id"], ts, j["id"]))
    else:
        conn.execute("UPDATE job SET stage_id = ?, updated_at = ? WHERE id = ?",
                     (target["id"], ts, j["id"]))
    conn.commit()
    return job_view(conn, code)


def log_uat(conn, job_code, verdict, note) -> None:
    conn.execute("INSERT INTO uat (job_code, verdict, note, at) VALUES (?,?,?,?)",
                 (job_code, verdict, note, now()))
    conn.commit()

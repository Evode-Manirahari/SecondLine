#
# SecondLine — business brain (persistent backend).
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""SecondLine business brain: a real, persistent backend for the voice agent.

This replaces the flower-shop starter's in-memory `mock_backend` dicts with a
SQLite-backed store so the agent can *remember* across calls:

    customers      — caller identity + freeform notes
    preferences    — typed memory: allergies, likes, dislikes, delivery defaults
    orders         — past orders (powers "same as last time")
    tasks          — the owner's work queue (escalations, follow-ups, callbacks)
    transcripts    — every turn of every call (powers eval + audit)
    calls          — one row per call: model, latency, outcome
    sms_log        — outbound SMS the owner/customer received

The LLM is never trusted to invent business state. It must call typed tools
(see tools.py) that read and write through this module. The catalog and seed
customers live here too so the bot runs with zero external dependencies.

Design notes:
  * stdlib `sqlite3` only — no extra deps, deploys cleanly to Pipecat Cloud.
  * `check_same_thread=False` + a module lock so the async pipeline and the
    dashboard can share one DB file safely.
  * Phone numbers are stored in E.164 (`+14155551234`) to match Twilio.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# DB lives next to the code by default; override with SECONDLINE_DB for the
# dashboard or eval harness to point at the same file.
DB_PATH = os.environ.get("SECONDLINE_DB", str(Path(__file__).parent / "secondline.db"))

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _init_schema(_conn)
    return _conn


def _init_schema(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers (
            phone       TEXT PRIMARY KEY,
            name        TEXT,
            notes       TEXT,
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS preferences (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            phone       TEXT,
            kind        TEXT,   -- allergy | likes | dislikes | delivery | note
            value       TEXT,
            created_at  TEXT,
            UNIQUE(phone, kind, value)
        );
        CREATE TABLE IF NOT EXISTS orders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            phone        TEXT,
            items_json   TEXT,
            delivery_json TEXT,
            total        REAL,
            confirmation TEXT,
            status       TEXT,
            created_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            phone       TEXT,
            kind        TEXT,   -- escalation | followup | callback | order
            summary     TEXT,
            details_json TEXT,
            confidence  REAL,
            status      TEXT,   -- open | done
            due         TEXT,
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS transcripts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id     TEXT,
            phone       TEXT,
            role        TEXT,
            text        TEXT,
            ts          TEXT
        );
        CREATE TABLE IF NOT EXISTS calls (
            call_id          TEXT PRIMARY KEY,
            phone            TEXT,
            model            TEXT,
            started_at       TEXT,
            ended_at         TEXT,
            outcome          TEXT,
            first_response_ms INTEGER
        );
        CREATE TABLE IF NOT EXISTS sms_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            to_num  TEXT,
            body    TEXT,
            status  TEXT,
            ts      TEXT
        );
        """
    )
    c.commit()


# ── catalog ────────────────────────────────────────────────────────────────
# Kept in-process (a real deploy would read this from the shop's POS). Each
# bouquet lists the flowers it contains so the allergen validation rule can
# match a customer's allergy against what's actually in the arrangement.

CATALOG: dict[str, dict] = {
    "spring sunshine": {
        "price": 45.00, "description": "Yellow tulips and daffodils",
        "flowers": ["tulips", "daffodils"], "in_stock": True,
        "occasions": ["birthday", "thank you", "get well", "mother's day", "spring"],
        "on_special": False,
    },
    "rose romance": {
        "price": 65.00, "description": "A dozen red roses with baby's breath",
        "flowers": ["roses", "baby's breath"], "in_stock": True,
        "occasions": ["valentine's day", "anniversary", "romance", "date night"],
        "on_special": False,
    },
    "wildflower medley": {
        "price": 38.00, "description": "Mixed seasonal wildflowers",
        "flowers": ["wildflowers"], "in_stock": True,
        "occasions": ["birthday", "thank you", "just because", "housewarming"],
        "on_special": True,
    },
    "lily elegance": {
        "price": 55.00, "description": "White lilies and greenery",
        "flowers": ["lilies"], "in_stock": True,
        "occasions": ["sympathy", "funeral", "remembrance"],
        "on_special": False,
    },
    "succulent garden": {
        "price": 42.00, "description": "Assorted succulents in a ceramic pot",
        "flowers": ["succulents"], "in_stock": True,
        "occasions": ["housewarming", "office", "thank you", "low maintenance"],
        "on_special": False,
    },
    "mother's day pastels": {
        "price": 58.00, "description": "Pink peonies, lavender, and white roses",
        "flowers": ["peonies", "lavender", "roses"], "in_stock": True,
        "occasions": ["mother's day", "birthday", "thank you"],
        "on_special": False,
    },
    "birthday brights": {
        "price": 48.00, "description": "Sunflowers, gerbera daisies, and orange roses",
        "flowers": ["sunflowers", "gerbera daisies", "roses"], "in_stock": True,
        "occasions": ["birthday", "congratulations", "thank you"],
        "on_special": True,
    },
    "sympathy whites": {
        "price": 70.00, "description": "White lilies, roses, and chrysanthemums",
        "flowers": ["lilies", "roses", "chrysanthemums"], "in_stock": True,
        "occasions": ["sympathy", "funeral", "remembrance", "condolences"],
        "on_special": False,
    },
    "garden party": {
        "price": 52.00, "description": "Hydrangeas, snapdragons, and stock",
        "flowers": ["hydrangeas", "snapdragons", "stock"], "in_stock": True,
        "occasions": ["wedding", "shower", "birthday", "thank you"],
        "on_special": False,
    },
    "graduation gold": {
        "price": 48.00, "description": "Sunflowers, yellow roses, and billy balls",
        "flowers": ["sunflowers", "roses", "billy balls"], "in_stock": True,
        "occasions": ["graduation", "congratulations", "achievement"],
        "on_special": False,
    },
    "tulip tower": {
        "price": 40.00, "description": "Assorted spring tulips",
        "flowers": ["tulips"], "in_stock": True,
        "occasions": ["spring", "easter", "just because", "thinking of you"],
        "on_special": False,
    },
}


def allergens_in_bouquet(bouquet_name: str, allergies: list[str]) -> list[str]:
    """Return the list of a customer's allergens present in a bouquet.

    Matches each allergy term against the bouquet's flower list AND its
    description, so "lilies" / "lily" both catch "lily elegance" and the lilies
    inside "sympathy whites". This is the data behind the allergen safety rule
    the self-improvement loop installs (see eval/improve.py).
    """
    info = CATALOG.get(bouquet_name.lower())
    if not info:
        return []
    haystack = (info["description"] + " " + " ".join(info["flowers"])).lower()
    hits = []
    for a in allergies:
        term = a.strip().lower().rstrip("s")  # crude singularize: lilies->lilie? keep stem
        stem = a.strip().lower()
        if stem and (stem in haystack or term in haystack):
            hits.append(a)
    return hits


# ── customers + memory ───────────────────────────────────────────────────────

def get_or_create_customer(phone: str, name: str | None = None) -> dict:
    if not phone:
        phone = "anonymous"
    with _lock:
        c = conn()
        row = c.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO customers(phone, name, notes, created_at) VALUES(?,?,?,?)",
                (phone, name, "", _now()),
            )
            c.commit()
            row = c.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
        elif name and not row["name"]:
            c.execute("UPDATE customers SET name=? WHERE phone=?", (name, phone))
            c.commit()
        return dict(row)


def get_customer_memory(phone: str) -> dict | None:
    """Everything the shop remembers about a caller, or None if brand new."""
    if not phone:
        return None
    with _lock:
        c = conn()
        cust = c.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
        if cust is None:
            return None
        prefs = c.execute(
            "SELECT kind, value FROM preferences WHERE phone=? ORDER BY id", (phone,)
        ).fetchall()
        last = c.execute(
            "SELECT * FROM orders WHERE phone=? ORDER BY id DESC LIMIT 1", (phone,)
        ).fetchone()
        memory = {
            "phone": phone,
            "name": cust["name"],
            "notes": cust["notes"],
            "allergies": [p["value"] for p in prefs if p["kind"] == "allergy"],
            "likes": [p["value"] for p in prefs if p["kind"] == "likes"],
            "dislikes": [p["value"] for p in prefs if p["kind"] == "dislikes"],
            "delivery_defaults": [p["value"] for p in prefs if p["kind"] == "delivery"],
            "last_order": None,
        }
        if last:
            memory["last_order"] = {
                "items": json.loads(last["items_json"]),
                "delivery": json.loads(last["delivery_json"]) if last["delivery_json"] else None,
                "confirmation": last["confirmation"],
                "when": last["created_at"],
            }
        return memory


def add_preference(phone: str, kind: str, value: str) -> None:
    if not (phone and value):
        return
    with _lock:
        c = conn()
        get_or_create_customer(phone)
        try:
            c.execute(
                "INSERT OR IGNORE INTO preferences(phone, kind, value, created_at) VALUES(?,?,?,?)",
                (phone, kind, value.strip(), _now()),
            )
            c.commit()
        except sqlite3.IntegrityError:
            pass


def set_customer_name(phone: str, name: str) -> None:
    with _lock:
        c = conn()
        get_or_create_customer(phone)
        c.execute("UPDATE customers SET name=? WHERE phone=?", (name, phone))
        c.commit()


# ── orders ─────────────────────────────────────────────────────────────────

def record_order(phone: str, items: list, delivery: dict | None, total: float,
                 confirmation: str, status: str = "placed") -> int:
    with _lock:
        c = conn()
        get_or_create_customer(phone)
        cur = c.execute(
            "INSERT INTO orders(phone, items_json, delivery_json, total, confirmation, status, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (phone, json.dumps(items), json.dumps(delivery) if delivery else None,
             total, confirmation, status, _now()),
        )
        c.commit()
        return cur.lastrowid


# ── tasks (owner work queue) ─────────────────────────────────────────────────

def create_task(phone: str, kind: str, summary: str, details: dict | None = None,
                confidence: float = 1.0, due: str | None = None) -> int:
    with _lock:
        c = conn()
        cur = c.execute(
            "INSERT INTO tasks(phone, kind, summary, details_json, confidence, status, due, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (phone, kind, summary, json.dumps(details or {}), confidence, "open", due, _now()),
        )
        c.commit()
        return cur.lastrowid


def list_tasks(status: str | None = "open") -> list[dict]:
    with _lock:
        c = conn()
        if status:
            rows = c.execute("SELECT * FROM tasks WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


# ── call + transcript logging ────────────────────────────────────────────────

def start_call(call_id: str, phone: str, model: str) -> None:
    with _lock:
        c = conn()
        c.execute(
            "INSERT OR REPLACE INTO calls(call_id, phone, model, started_at, ended_at, outcome, first_response_ms) "
            "VALUES(?,?,?,?,?,?,?)",
            (call_id, phone, model, _now(), None, "in_progress", None),
        )
        c.commit()


def end_call(call_id: str, outcome: str, first_response_ms: int | None = None) -> None:
    with _lock:
        c = conn()
        c.execute(
            "UPDATE calls SET ended_at=?, outcome=?, first_response_ms=COALESCE(?, first_response_ms) "
            "WHERE call_id=?",
            (_now(), outcome, first_response_ms, call_id),
        )
        c.commit()


def log_turn(call_id: str, phone: str, role: str, text: str) -> None:
    with _lock:
        c = conn()
        c.execute(
            "INSERT INTO transcripts(call_id, phone, role, text, ts) VALUES(?,?,?,?,?)",
            (call_id, phone, role, text, _now()),
        )
        c.commit()


def log_sms(to_num: str, body: str, status: str) -> None:
    with _lock:
        c = conn()
        c.execute(
            "INSERT INTO sms_log(to_num, body, status, ts) VALUES(?,?,?,?)",
            (to_num, body, status, _now()),
        )
        c.commit()


# ── dashboard read helpers ────────────────────────────────────────────────────

def recent_calls(limit: int = 25) -> list[dict]:
    with _lock:
        c = conn()
        rows = c.execute("SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def transcript_for(call_id: str) -> list[dict]:
    with _lock:
        c = conn()
        rows = c.execute(
            "SELECT role, text, ts FROM transcripts WHERE call_id=? ORDER BY id", (call_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def all_customers() -> list[dict]:
    with _lock:
        out = []
        for cust in conn().execute("SELECT phone FROM customers ORDER BY phone").fetchall():
            mem = get_customer_memory(cust["phone"])
            if mem:
                out.append(mem)
        return out


# ── seed ─────────────────────────────────────────────────────────────────────

# A repeat customer roster. The DEMO_CALLER is the star of the killer demo:
# returning caller, last order on file, and a remembered allergy.
SEED_CUSTOMERS = [
    # phone, name, last_order_items, last_delivery, allergies, likes
    ("+14155551234", "Alex Rivera",
     [{"bouquet": "rose romance", "quantity": 1, "price": 65.0}],
     {"recipient_name": "Mom", "address": "412 Pine St", "delivery_date": "last Friday"},
     ["lilies"], ["roses"]),
    ("+14155555678", "Jordan Lee",
     [{"bouquet": "wildflower medley", "quantity": 2, "price": 38.0}],
     {"recipient_name": "Sam", "address": "88 Oak Ave", "delivery_date": "last Tuesday"},
     [], ["wildflowers"]),
    ("+14155550111", "Priya Patel",
     [{"bouquet": "mother's day pastels", "quantity": 1, "price": 58.0}],
     {"recipient_name": "Mom", "address": "9 Birch Ln", "delivery_date": "last month"},
     ["pollen"], ["peonies"]),
    ("+14155550222", "Marcus Chen",
     [{"bouquet": "succulent garden", "quantity": 1, "price": 42.0}],
     None, [], ["succulents"]),
    ("+14155550333", "Dana Wright",
     [{"bouquet": "birthday brights", "quantity": 1, "price": 48.0}],
     {"recipient_name": "Casey", "address": "300 Elm St", "delivery_date": "last week"},
     [], ["sunflowers"]),
]


def seed(reset: bool = False) -> None:
    """Populate the catalog (implicit) and repeat-customer roster.

    The customers' "last orders" are what make the repeat-caller demo feel
    magical: caller +14155551234 ("same as last time, but no lilies") already
    has a rose romance on file and a remembered lily allergy.
    """
    with _lock:
        c = conn()
        if reset:
            for t in ("customers", "preferences", "orders", "tasks", "transcripts", "calls", "sms_log"):
                c.execute(f"DELETE FROM {t}")
            c.commit()
        for phone, name, items, delivery, allergies, likes in SEED_CUSTOMERS:
            get_or_create_customer(phone, name)
            for a in allergies:
                add_preference(phone, "allergy", a)
            for l in likes:
                add_preference(phone, "likes", l)
            # only seed an order if they don't already have one
            existing = c.execute("SELECT 1 FROM orders WHERE phone=? LIMIT 1", (phone,)).fetchone()
            if not existing:
                total = sum(i["price"] * i["quantity"] for i in items)
                record_order(phone, items, delivery, total, f"FLW-{abs(hash(phone)) % 900000 + 100000}")
        c.commit()


if __name__ == "__main__":
    import sys
    reset = "--reset" in sys.argv
    seed(reset=reset)
    print(f"Seeded SecondLine DB at {DB_PATH} (reset={reset})")
    for m in all_customers():
        print(f"  {m['phone']}  {m['name']:14}  allergies={m['allergies']}  "
              f"last={m['last_order']['items'] if m['last_order'] else None}")

"""
database.py — SQLite backend for BioPrint.

All persistence lives here: users, raw enrollment samples, computed
behavioral profiles, and a full audit log of login attempts (with the
feature vector + explanation captured at decision time).
"""

import sqlite3
import json
import time
import os
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bioprint.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    enrolled        INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS enrollment_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    round_index     INTEGER NOT NULL,
    features_json   TEXT NOT NULL,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    features_json   TEXT NOT NULL,
    sample_count    INTEGER NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER REFERENCES users(id) ON DELETE CASCADE,
    username_tried      TEXT NOT NULL,
    timestamp           REAL NOT NULL,
    password_ok         INTEGER NOT NULL,
    is_bot              INTEGER NOT NULL,
    confidence          REAL NOT NULL,
    accepted            INTEGER NOT NULL,
    features_json       TEXT NOT NULL,
    explanation_json     TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------- users --

def create_user(username, password_hash):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, enrolled, created_at) "
            "VALUES (?, ?, 0, ?)",
            (username, password_hash, time.time()),
        )
        return cur.lastrowid


def get_user(username):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None


def mark_enrolled(user_id):
    with get_conn() as conn:
        conn.execute("UPDATE users SET enrolled = 1 WHERE id = ?", (user_id,))


# ------------------------------------------------------- enrollment data --

def add_enrollment_sample(user_id, round_index, features: dict):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO enrollment_samples (user_id, round_index, features_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, round_index, json.dumps(features), time.time()),
        )


def get_enrollment_samples(user_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT round_index, features_json FROM enrollment_samples "
            "WHERE user_id = ? ORDER BY round_index",
            (user_id,),
        ).fetchall()
        return [json.loads(r["features_json"]) for r in rows]


def clear_enrollment_samples(user_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM enrollment_samples WHERE user_id = ?", (user_id,))


# ------------------------------------------------------------- profiles --

def save_profile(user_id, features: dict, sample_count: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO profiles (user_id, features_json, sample_count, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "features_json = excluded.features_json, "
            "sample_count = excluded.sample_count, "
            "updated_at = excluded.updated_at",
            (user_id, json.dumps(features), sample_count, time.time()),
        )


def get_profile(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["features"] = json.loads(d.pop("features_json"))
        return d


# -------------------------------------------------------- login attempts --

def log_attempt(user_id, username_tried, password_ok, is_bot, confidence,
                 accepted, features: dict, explanation: list):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO login_attempts "
            "(user_id, username_tried, timestamp, password_ok, is_bot, confidence, "
            " accepted, features_json, explanation_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, username_tried, time.time(), int(password_ok), int(is_bot),
                confidence, int(accepted), json.dumps(features), json.dumps(explanation),
            ),
        )
        return cur.lastrowid


def get_attempt(attempt_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM login_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["features"] = json.loads(d.pop("features_json"))
        d["explanation"] = json.loads(d.pop("explanation_json"))
        return d


def get_attempts_for_user(user_id, limit=25):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, password_ok, is_bot, confidence, accepted "
            "FROM login_attempts WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

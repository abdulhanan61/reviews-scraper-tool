"""
SQLite database layer for the Review Scraper Tool.

Two tables, matching Section 3.1 of the project plan:
  - jobs:    one row per scrape job (status, progress, params)
  - reviews: one row per scraped review, linked to its job

No manual setup needed -- init_db() creates the .db file and both tables
the first time it's called (Flask calls this once on startup in app.py).

Usage from elsewhere in the app:
    from database.db import (
        init_db, create_job, update_job_progress, update_job_status,
        save_reviews, get_job, list_jobs, delete_job
    )
"""

import sqlite3
import json
import uuid
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reviews.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # lets us access columns by name, e.g. row["status"]
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Creates jobs + reviews tables if they don't already exist. Safe to call every startup."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id              TEXT PRIMARY KEY,
                platform        TEXT NOT NULL,
                job_params      TEXT NOT NULL,       -- JSON blob: {business_name, location, max_reviews, ...}
                status          TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed
                branch_current  INTEGER DEFAULT 0,
                branch_total    INTEGER DEFAULT 0,
                reviews_so_far  INTEGER DEFAULT 0,
                total_reviews   INTEGER DEFAULT 0,
                error_message   TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id        TEXT NOT NULL,
                name          TEXT,
                rating        TEXT,
                review_text   TEXT,
                date          TEXT,
                location      TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_job_id ON reviews(job_id)")
        conn.commit()
    finally:
        conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


# ------------------------------- jobs -------------------------------

def create_job(platform, job_params):
    """Creates a new job row with status 'queued'. Returns the new job_id."""
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO jobs (id, platform, job_params, status, created_at, updated_at)
               VALUES (?, ?, ?, 'queued', ?, ?)""",
            (job_id, platform, json.dumps(job_params), _now(), _now())
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


def update_job_status(job_id, status, error_message=None):
    """status: 'queued' | 'running' | 'done' | 'failed'"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE jobs SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
            (status, error_message, _now(), job_id)
        )
        conn.commit()
    finally:
        conn.close()


def update_job_progress(job_id, branch_current, branch_total, reviews_so_far):
    """Called from the scraper's progress_callback during a run -- feeds GET /api/jobs/<id>/status."""
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE jobs
               SET branch_current = ?, branch_total = ?, reviews_so_far = ?, updated_at = ?
               WHERE id = ?""",
            (branch_current, branch_total, reviews_so_far, _now(), job_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_job(job_id):
    """Returns a dict for one job, or None if it doesn't exist."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job_row_to_dict(row) if row else None
    finally:
        conn.close()


def list_jobs():
    """Returns all jobs, most recent first."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [_job_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def delete_job(job_id):
    """Deletes a job and all its reviews (foreign key cascade). Returns True if a row was deleted."""
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _job_row_to_dict(row):
    d = dict(row)
    d["job_params"] = json.loads(d["job_params"])
    return d


# ------------------------------ reviews ------------------------------

def save_reviews(job_id, reviews):
    """
    reviews: list of dicts with keys Name/Username, Rating, Review, Date, Location
    (the exact shape every plugin's .run() returns). Also updates total_reviews on the job.
    """
    conn = get_connection()
    try:
        conn.executemany(
            """INSERT INTO reviews (job_id, name, rating, review_text, date, location)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (job_id, r.get("Name/Username"), r.get("Rating"),
                 r.get("Review"), r.get("Date"), r.get("Location"))
                for r in reviews
            ]
        )
        conn.execute(
            "UPDATE jobs SET total_reviews = ?, updated_at = ? WHERE id = ?",
            (len(reviews), _now(), job_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_reviews_for_job(job_id):
    """Returns all reviews for a job, in the plugin's original dict shape (for exports)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM reviews WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
        return [
            {
                "Name/Username": r["name"],
                "Rating": r["rating"],
                "Review": r["review_text"],
                "Date": r["date"],
                "Location": r["location"],
            }
            for r in rows
        ]
    finally:
        conn.close()
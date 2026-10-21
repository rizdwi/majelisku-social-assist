import sqlite3
import contextlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from config import DB_PATH

@contextlib.contextmanager
def get_db(db_path: Optional[str] = None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db(db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_posts (
                post_id TEXT PRIMARY KEY,
                author TEXT,
                text TEXT,
                url TEXT,
                created_at TEXT,
                fetched_at TEXT,
                score INTEGER,
                status TEXT DEFAULT 'pending'
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS drafts (
                post_id TEXT PRIMARY KEY REFERENCES raw_posts(post_id),
                is_relevant BOOLEAN,
                ai_reasoning TEXT,
                draft_reply TEXT,
                include_bridging BOOLEAN,
                final_reply TEXT,
                review_status TEXT DEFAULT 'pending_review',
                reviewed_at TEXT
            );
        """)
        conn.commit()

def save_raw_posts(posts: List[Dict[str, Any]], db_path: Optional[str] = None) -> int:
    inserted_count = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        for p in posts:
            cursor.execute(
                """
                INSERT OR IGNORE INTO raw_posts (post_id, author, text, url, created_at, fetched_at, score, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    p["post_id"],
                    p.get("author", ""),
                    p.get("text", ""),
                    p.get("url", ""),
                    p.get("created_at", now_iso),
                    p.get("fetched_at", now_iso),
                    p.get("score", 0)
                )
            )
            if cursor.rowcount > 0:
                inserted_count += 1
        conn.commit()
    return inserted_count

def get_pending_posts(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM raw_posts WHERE status = 'pending'")
        return [dict(row) for row in cursor.fetchall()]

def update_post_score_and_status(post_id: str, score: int, status: str, db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE raw_posts SET score = ?, status = ? WHERE post_id = ?",
            (score, status, post_id)
        )
        conn.commit()

def get_candidates_without_draft(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.* FROM raw_posts r
            LEFT JOIN drafts d ON r.post_id = d.post_id
            WHERE r.status = 'candidate' AND d.post_id IS NULL
        """)
        return [dict(row) for row in cursor.fetchall()]

def save_draft(
    post_id: str,
    is_relevant: bool,
    ai_reasoning: str,
    draft_reply: str,
    include_bridging: bool,
    db_path: Optional[str] = None
) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO drafts (
                post_id, is_relevant, ai_reasoning, draft_reply, include_bridging, review_status
            ) VALUES (?, ?, ?, ?, ?, 'pending_review')
            """,
            (post_id, is_relevant, ai_reasoning, draft_reply, include_bridging)
        )
        conn.commit()

def get_review_queue(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.post_id, r.author, r.text, r.url, r.created_at, r.score,
                   d.is_relevant, d.ai_reasoning, d.draft_reply, d.include_bridging,
                   d.final_reply, d.review_status, d.reviewed_at
            FROM drafts d
            JOIN raw_posts r ON d.post_id = r.post_id
            WHERE d.review_status = 'pending_review'
            ORDER BY r.score DESC, r.created_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

def get_approved_queue(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.post_id, r.author, r.text, r.url, r.created_at, r.score,
                   d.is_relevant, d.ai_reasoning, d.draft_reply, d.include_bridging,
                   d.final_reply, d.review_status, d.reviewed_at
            FROM drafts d
            JOIN raw_posts r ON d.post_id = r.post_id
            WHERE d.review_status = 'approved'
            ORDER BY d.reviewed_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

def update_draft_review(
    post_id: str,
    review_status: str,
    final_reply: Optional[str] = None,
    db_path: Optional[str] = None
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE drafts
            SET review_status = ?, final_reply = ?, reviewed_at = ?
            WHERE post_id = ?
            """,
            (review_status, final_reply, now_iso, post_id)
        )
        conn.commit()

def get_stats(db_path: Optional[str] = None) -> Dict[str, int]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM raw_posts")
        total_raw = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM raw_posts WHERE status = 'candidate'")
        total_candidates = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM drafts WHERE review_status = 'pending_review'")
        pending_review = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM drafts WHERE review_status = 'approved'")
        approved = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM drafts WHERE review_status = 'skipped'")
        skipped = cursor.fetchone()[0]

        return {
            "total_raw": total_raw,
            "total_candidates": total_candidates,
            "pending_review": pending_review,
            "approved": approved,
            "skipped": skipped
        }

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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
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

def get_skipped_queue(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.post_id, r.author, r.text, r.url, r.created_at, r.score,
                   d.is_relevant, d.ai_reasoning, d.draft_reply, d.include_bridging,
                   d.final_reply, d.review_status, d.reviewed_at
            FROM drafts d
            JOIN raw_posts r ON d.post_id = r.post_id
            WHERE d.review_status = 'skipped'
            ORDER BY d.reviewed_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

def reset_draft_to_pending(post_id: str, db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE drafts
            SET review_status = 'pending_review', reviewed_at = NULL
            WHERE post_id = ?
        """, (post_id,))
        conn.commit()

def reset_all_drafts_to_pending(db_path: Optional[str] = None) -> int:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE drafts
            SET review_status = 'pending_review', reviewed_at = NULL
        """)
        conn.commit()
        return cursor.rowcount

def clear_mock_posts(db_path: Optional[str] = None) -> int:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM drafts WHERE post_id LIKE '%mock%'")
        cursor.execute("DELETE FROM raw_posts WHERE post_id LIKE '%mock%'")
        conn.commit()
        return cursor.rowcount

def wipe_all_database_posts(db_path: Optional[str] = None) -> int:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM drafts")
        cursor.execute("DELETE FROM raw_posts")
        conn.commit()
        return cursor.rowcount

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

def get_setting(key: str, default: Optional[str] = None, db_path: Optional[str] = None) -> Optional[str]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row and row["value"] is not None:
            return row["value"]
        return default

def set_setting(key: str, value: str, db_path: Optional[str] = None) -> None:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def get_all_settings(db_path: Optional[str] = None) -> Dict[str, str]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        return {row["key"]: row["value"] for row in cursor.fetchall()}

def get_all_raw_posts(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.post_id, r.author, r.text, r.url, r.created_at, r.fetched_at, r.score, r.status,
                   d.review_status, d.draft_reply, d.ai_reasoning
            FROM raw_posts r
            LEFT JOIN drafts d ON r.post_id = d.post_id
            ORDER BY r.created_at DESC, r.fetched_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

def force_candidate_and_draft(post_id: str, db_path: Optional[str] = None) -> bool:
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE raw_posts SET status = 'candidate' WHERE post_id = ?", (post_id,))
        cursor.execute("UPDATE drafts SET review_status = 'pending_review' WHERE post_id = ?", (post_id,))
        conn.commit()
    from generate_draft import process_candidates
    process_candidates(db_path)
    return True

def reclassify_all_raw_posts(new_threshold: Optional[int] = None, db_path: Optional[str] = None) -> int:
    if new_threshold is not None:
        set_setting("SCORE_THRESHOLD", str(new_threshold), db_path)
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE raw_posts SET status = 'pending'
            WHERE post_id NOT IN (SELECT post_id FROM drafts WHERE review_status = 'approved')
        """)
        conn.commit()
    from classifier import classify_pending_posts
    from generate_draft import process_candidates
    classified = classify_pending_posts(db_path)
    process_candidates(db_path)
    return classified

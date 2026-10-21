import re
from typing import List, Tuple, Optional
from config import (
    MUST_HAVE_ACTION,
    MUST_HAVE_OBJECT,
    BOOSTERS,
    EXCLUDE_PATTERNS,
    SCORE_THRESHOLD,
    DB_PATH
)
from db import get_pending_posts, update_post_score_and_status

def score_post(text: str) -> Tuple[int, List[str]]:
    score = 0
    reasons = []
    text_lower = text.lower()

    # Cek exclude pattern (sarkasme, promo, dsb)
    for pattern in EXCLUDE_PATTERNS:
        if pattern in text_lower:
            score -= 5
            reasons.append(f"Exclude pattern terdeteksi: '{pattern}' (-5)")

    # Pisahkan ke kalimat untuk cek kombinasi action + object
    sentences = re.split(r"[.!?\n]+", text_lower)
    matched_action_object = False
    for sent in sentences:
        has_action = any(act in sent for act in MUST_HAVE_ACTION)
        has_object = any(obj in sent for obj in MUST_HAVE_OBJECT)
        if has_action and has_object:
            matched_action_object = True
            break

    if matched_action_object:
        score += 3
        reasons.append("Terdapat kombinasi kata aksi dan objek kajian dalam satu kalimat (+3)")
    else:
        # Cek apakah ada objek kajian meskipun action di kalimat berbeda
        has_obj_anywhere = any(obj in text_lower for obj in MUST_HAVE_OBJECT)
        if has_obj_anywhere:
            score += 1
            reasons.append("Menyebutkan topik kajian (+1)")

    # Cek kata penguat (boosters)
    matched_boosters = [b for b in BOOSTERS if b in text_lower]
    if matched_boosters:
        booster_score = min(len(matched_boosters), 3)
        score += booster_score
        reasons.append(f"Booster relevan: {', '.join(matched_boosters)} (+{booster_score})")

    # Cek apakah berbentuk pertanyaan
    if "?" in text or any(q in text_lower for q in ["apakah", "dimana", "ada yang tau", "rekomendasi"]):
        score += 1
        reasons.append("Format pertanyaan/mencari informasi (+1)")

    return score, reasons

def classify_pending_posts(db_path: Optional[str] = None) -> int:
    pending = get_pending_posts(db_path or DB_PATH)
    processed_count = 0

    for post in pending:
        score, _ = score_post(post["text"])
        status = "candidate" if score >= SCORE_THRESHOLD else "rejected"
        update_post_score_and_status(post["post_id"], score, status, db_path or DB_PATH)
        processed_count += 1

    return processed_count

if __name__ == "__main__":
    count = classify_pending_posts()
    print(f"Selesai mengklasifikasi {count} post pending.")

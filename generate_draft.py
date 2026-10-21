import json
import logging
import re
from typing import Dict, Any, Optional
import requests
from config import AI_API_KEY, AI_MODEL, AI_BASE_URL, DB_PATH
from db import get_candidates_without_draft, save_draft

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Kamu adalah asisten seleksi dan penyusun draft balasan untuk Majelisku (majelisku.web.id).

Tugasmu memiliki dua tahap dalam satu respons:
Tahap 1: Validasi Relevansi
Tentukan apakah postingan benar-benar pertanyaan genuine dari seseorang yang sedang mencari kajian/tempat ngaji/majelis ilmu.
- is_relevant = true jika TS secara eksplisit mencari info/rekomendasi kajian/tempat ngaji.
- is_relevant = false jika sarkasme, bercanda, debat abstrak, atau tidak ada niat mencari kajian. Jika ragu, pilih false.

Tahap 2: Penyusunan Draft Balasan (HANYA jika is_relevant = true)
Jika relevan, susun draft balasan dengan aturan:
1. Jawab pertanyaan TS secara substantif dan jujur. Jangan jawaban kosong numpang promosi.
2. Sertakan rujukan ke Majelisku.web.id secara natural, ramah, dan bukan hard-sell (contoh: "Bisa juga cek di Majelisku.web.id untuk daftar jadwal kajian sunnah terdekat kak").
3. Nada santai dan tulus khas Threads, 1 sampai 3 kalimat saja.
4. JANGAN mengarang pengalaman pribadi palsu (misal "dulu aku juga bingung...").
5. Jika is_relevant = false, isi draft_reply dengan string kosong dan include_bridging = false.

Output HARUS berupa JSON murni dengan format:
{
  "is_relevant": true/false,
  "reasoning": "alasan singkat 1 kalimat dalam bahasa Indonesia",
  "draft_reply": "teks balasan jika relevan, atau kosong jika tidak relevan",
  "include_bridging": true/false
}
"""

def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    # Bersihkan markdown codeblock jika ada
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Coba cari pola {...} di dalam teks
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return None

def call_ai_api(post_text: str) -> Dict[str, Any]:
    # Jika API key belum diisi, gunakan fallback deterministik yang aman
    if not AI_API_KEY:
        # Heuristic dasar untuk testing offline
        return {
            "is_relevant": True,
            "reasoning": "Validasi otomatis (kredensial AI belum diisi di .env, mode fallback aktif).",
            "draft_reply": "Assalamu'alaikum kak, untuk info jadwal kajian sunnah terdekat bisa coba cek di Majelisku.web.id ya. Semoga dimudahkan dan bermanfaat.",
            "include_bridging": True
        }

    # Dukungan Anthropic API
    if "claude" in AI_MODEL.lower() or AI_API_KEY.startswith("sk-ant"):
        url = AI_BASE_URL or "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": AI_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        payload = {
            "model": AI_MODEL,
            "max_tokens": 500,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": f"Postingan Threads:\n\"{post_text}\""}
            ]
        }
        res = requests.post(url, headers=headers, json=payload, timeout=25)
        res.raise_for_status()
        data = res.json()
        raw_content = data["content"][0]["text"]
        parsed = extract_json_from_text(raw_content)
        if parsed:
            return parsed

    # Dukungan OpenAI / OpenRouter / endpoint kompatibel
    url = AI_BASE_URL or "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": AI_MODEL if "claude" not in AI_MODEL.lower() else "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Postingan Threads:\n\"{post_text}\""}
        ],
        "temperature": 0.3
    }
    res = requests.post(url, headers=headers, json=payload, timeout=25)
    res.raise_for_status()
    data = res.json()
    raw_content = data["choices"][0]["message"]["content"]
    parsed = extract_json_from_text(raw_content)
    if parsed:
        return parsed

    return {
        "is_relevant": False,
        "reasoning": "Gagal mengurai format JSON dari respons AI",
        "draft_reply": "",
        "include_bridging": False
    }

def process_candidates(db_path: Optional[str] = None) -> int:
    candidates = get_candidates_without_draft(db_path or DB_PATH)
    processed_count = 0

    for cand in candidates:
        try:
            result = call_ai_api(cand["text"])
            save_draft(
                post_id=cand["post_id"],
                is_relevant=bool(result.get("is_relevant", False)),
                ai_reasoning=str(result.get("reasoning", "")),
                draft_reply=str(result.get("draft_reply", "")),
                include_bridging=bool(result.get("include_bridging", False)),
                db_path=db_path or DB_PATH
            )
            processed_count += 1
        except Exception as e:
            logger.error(f"Gagal memproses post {cand['post_id']}: {e}")

    return processed_count

if __name__ == "__main__":
    count = process_candidates()
    print(f"Selesai menghasilkan {count} draft balasan.")

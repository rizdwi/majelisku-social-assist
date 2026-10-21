import hashlib
import json
import logging
import re
from typing import Dict, Any, Optional
import requests
from config import (
    get_ai_api_key,
    get_ai_model,
    get_ai_base_url,
    DB_PATH
)
from db import get_candidates_without_draft, save_draft

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Kamu adalah asisten seleksi dan penyusun draft balasan untuk Majelisku (majelisku.web.id).

Tugasmu memiliki TIGA tahap dalam satu respons:

Tahap 1: BACA TS (Thread Starter) DULU — jangan langsung menulis.
Petakan dari postingan TS:
- maksud: apa yang sebenarnya sedang ia cari/tanyakan (jadwal? lokasi terdekat? teman berangkat? komunitas? kajian khusus akhwat/anak/pemula?)
- tujuan: hasil akhir yang ia harapkan (mis. bisa berangkat pekan ini, tidak berangkat sendirian lagi, mulai rutin ngaji)
- emosi & nada: antusias/semangat, bingung, ragu/malu-malu, kesepian (berangkat sendiri terus), santai-gaul, atau serius-formal
- konteks spesifik: kota/daerah yang disebut, kondisinya (baru pindah, baru hijrah, sendirian, untuk anak, dsb.)
Isi ketiganya ringkas di field "ts_read". Baca dari teksnya — JANGAN mengarang konteks yang tidak ada di postingan.

Tahap 2: Validasi Relevansi
- is_relevant = true jika TS secara eksplisit mencari info/rekomendasi kajian/tempat ngaji.
- is_relevant = false jika sarkasme, bercanda, debat abstrak, atau tidak ada niat mencari kajian. Jika ragu, pilih false.

Tahap 3: Penyusunan Draft Balasan (HANYA jika is_relevant = true)
Susun draft yang SELARAS dengan hasil bacaanmu di Tahap 1:
1. CERMINKAN emosi TS: ia antusias -> balas dengan energi yang sama; ia ragu/malu -> balas menenangkan dan menyambut; ia kesepian -> tekankan sisi kebersamaan/komunitas; ia santai-gaul -> balas santai juga (tetap sopan). Sapaan mengikuti gaya TS (ia pakai "kak", balas "kak"; petunjuk akhwat -> "ukhti" boleh; tanpa petunjuk -> sapaan netral hangat).
2. JAWAB maksudnya secara SPESIFIK: sebut ulang kota/daerah atau kondisi yang TS sebut (mis. "di Bandung tengah kota", "yang khusus akhwat", "buat yang baru mulai") supaya terasa dibaca, bukan dibalas massal. Jawab dulu substansinya, promosi belakangan.
3. Bridging ke Majelisku.web.id tetap natural dan BUKAN hard-sell, dan posisikan bervariasi (kadang di tengah, kadang di akhir) — jangan selalu kalimat yang sama. Kaitkan dengan kebutuhannya (mis. filter terdekat dari lokasinya, jadwal pekan ini).
4. 2 sampai 4 kalimat pendek khas Threads. DILARANG memakai pola pembuka yang sama untuk semua orang dan DILARANG mengarang pengalaman pribadi palsu (misal "dulu aku juga bingung...").
5. Jika is_relevant = false, isi draft_reply dengan string kosong dan include_bridging = false.

Output HARUS berupa JSON murni dengan format:
{
  "is_relevant": true/false,
  "ts_read": {"maksud": "...", "tujuan": "...", "emosi": "..."},
  "reasoning": "alasan singkat 1 kalimat dalam bahasa Indonesia",
  "draft_reply": "teks balasan jika relevan, atau kosong jika tidak relevan",
  "include_bridging": true/false
}
"""

# ---------------------------------------------------------------------------
# Fallback personal (dipakai bila AI tidak tersedia / error).
# Perintah user 2026-10-22: draft TIDAK BOLEH generik & sama semua — fallback
# pun wajib membaca sinyal maksud/kondisi/emosi dari teks TS dan menyusun
# balasan yang selaras, dengan variasi antar-postingan (dipilih deterministik
# dari hash teks agar postingan yang sama selalu mendapat draft yang sama).
# Reasoning tetap JUJUR berlabel FALLBACK TANPA AI (audit 2026-10-22).
# ---------------------------------------------------------------------------

_CITY_WORDS = [
    ("jakarta", "Jakarta"), ("bandung", "Bandung"), ("surabaya", "Surabaya"),
    ("medan", "Medan"), ("semarang", "Semarang"), ("makassar", "Makassar"),
    ("yogyakarta", "Yogyakarta"), ("jogja", "Yogyakarta"), ("depok", "Depok"),
    ("bekasi", "Bekasi"), ("tangerang", "Tangerang"), ("bogor", "Bogor"),
    ("palembang", "Palembang"), ("solo", "Solo"), ("malang", "Malang"),
    ("balikpapan", "Balikpapan"), ("banjarmasin", "Banjarmasin"),
    ("pontianak", "Pontianak"), ("padang", "Padang"), ("pekanbaru", "Pekanbaru"),
    ("lampung", "Lampung"), ("denpasar", "Denpasar"), ("bali", "Bali"),
    ("batam", "Batam"), ("cirebon", "Cirebon"), ("tasikmalaya", "Tasikmalaya"),
    ("karawang", "Karawang"), ("cikampek", "Cikampek"), ("purwakarta", "Purwakarta"),
    ("subang", "Subang"), ("jaktim", "Jakarta Timur"), ("jakut", "Jakarta Utara"),
    ("jaksel", "Jakarta Selatan"), ("jakbar", "Jakarta Barat"),
    ("jakpus", "Jakarta Pusat"), ("bdg", "Bandung"), ("cileunyi", "Cileunyi"),
    ("cimahi", "Cimahi"), ("duren sawit", "Duren Sawit"), ("bsd", "BSD"),
]

_SIGNAL_WORDS = {
    "akhwat": ["akhwat", "ukhti", "muslimah", "khusus wanita", "buat cewek", "perempuan"],
    "pemula": ["pemula", "baru hijrah", "hijrah", "baru mulai", "baru belajar", "mualaf", "awam"],
    "pindah": ["baru pindah", "pindah ke", "anak rantau", "merantau", "baru di kota", "baru tinggal"],
    "teman": ["bareng", "teman", "sendirian", "sendiri", "gabung", "komunitas", "ada yang mau"],
    "anak": ["untuk anak", "anak saya", "buat anak", "kajian anak"],
}

_EMPATHY_OPEN = {
    "pindah": [
        "Wah baru pindah ya kak — wajar banget masih nyari-nyari majelis yang cocok di sekitar situ.",
        "Baru pindah memang PR-nya nyari majelis baru ya kak, semoga segera ketemu yang cocok.",
    ],
    "pemula": [
        "MasyaAllah, semangat banget kak baru mulai — semoga Allah mudahkan langkah pertamanya.",
        "Buat yang baru mulai justru paling pas cari majelis yang ramah pemula, kak.",
    ],
    "teman": [
        "Iya kak, berangkat kajian bareng teman memang jauh lebih enak daripada sendirian.",
        "Betul kak, ada teman berangkat itu bikin lebih semangat dan istiqamah.",
    ],
    "akhwat": [
        "Ada kok kak kajian khusus akhwat — biasanya suasananya lebih nyaman, apalagi buat yang baru mulai.",
        "Kajian akhwat biasanya rutin juga kak, dan pesertanya sesama muslimah jadi lebih leluasa bertanya.",
    ],
    "anak": [
        "Buat anak memang perlu majelis yang materinya sesuai usia ya kak, semoga ketemu yang pas.",
    ],
    "default": [
        "Semoga dimudahkan ya kak nyarinya.",
        "Pertanyaan yang bagus kak — semoga segera ketemu majelis yang cocok.",
    ],
}

_BRIDGE = [
    "Coba cek di Majelisku.web.id kak, ada daftar kajian sunnah {tempat} yang bisa dicari yang paling dekat dari lokasi kakak.",
    "Di Majelisku.web.id kakak bisa lihat jadwal kajian sunnah {tempat} — tinggal pilih yang waktunya paling pas.",
    "Kakak bisa intip Majelisku.web.id, koleksi kajian sunnah {tempat} di sana cukup lengkap dan rutin diperbarui.",
]

_CLOSING = [
    "Semoga ketemu majelis yang cocok ya kak.",
    "Barakallahu fiik kak, semoga dimudahkan dan istiqamah.",
    "Semangat kak, semoga Allah mudahkan langkahnya.",
]


def _pick(variants, seed_text: str, salt: str) -> str:
    h = hashlib.md5(f"{salt}:{seed_text}".encode("utf-8")).hexdigest()
    return variants[int(h[:6], 16) % len(variants)]


def read_ts_signals(post_text: str) -> Dict[str, Any]:
    """Baca sinyal maksud/kondisi/emosi dari teks TS (heuristik fallback)."""
    low = f" {post_text.lower()} "
    city = None
    for kw, display in _CITY_WORDS:
        if kw in low:
            city = display
            break
    signals = [name for name, words in _SIGNAL_WORDS.items()
               if any(w in low for w in words)]
    # Kesepian eksplisit ("sendirian/sendiri") adalah inti emosi TS — sinyal
    # cari teman didahulukan agar kalimat empati menyorot itu.
    if "teman" in signals and ("sendirian" in low or "sendiri" in low):
        signals.remove("teman")
        signals.insert(0, "teman")
    if any(w in low for w in ["malu", "bingung", "ragu", "gapapa", "boleh tanya", "sungkan"]):
        emosi = "ragu/malu-malu, perlu disambut hangat"
    elif any(w in low for w in ["banget", "semangat", "pengen banget", "gas", "yuk"]):
        emosi = "antusias/semangat"
    elif any(w in low for w in ["dong", "yah", "yu", "nih", "sih", "wkwk"]):
        emosi = "santai-gaul"
    else:
        emosi = "netral bertanya"
    maksud = "mencari kajian sunnah"
    if city:
        maksud += f" di sekitar {city}"
    if "akhwat" in signals:
        maksud += " khusus akhwat"
    if "teman" in signals:
        maksud += " sekalian cari teman berangkat/komunitas"
    if "pemula" in signals:
        maksud += " untuk pemula/baru hijrah"
    if "anak" in signals:
        maksud += " untuk anak"
    return {"city": city, "signals": signals, "emosi": emosi, "maksud": maksud}


def fallback_draft(post_text: str, reason: str) -> Dict[str, Any]:
    """Draft personal berbasis sinyal teks — BUKAN satu template untuk semua."""
    sig = read_ts_signals(post_text)
    top = sig["signals"][0] if sig["signals"] else "default"
    open_line = _pick(_EMPATHY_OPEN.get(top, _EMPATHY_OPEN["default"]), post_text, "open")
    tempat = f"di sekitar {sig['city']}" if sig["city"] else "di berbagai kota"
    bridge = _pick(_BRIDGE, post_text, "bridge").format(tempat=tempat)
    closing = _pick(_CLOSING, post_text, "closing")
    return {
        "is_relevant": True,
        "reasoning": (
            f"FALLBACK TANPA AI: {reason} — draft disusun dari sinyal teks "
            f"(maksud: {sig['maksud']}; emosi: {sig['emosi']}), relevansi BELUM "
            f"divalidasi AI. Wajib dicek manual."
        ),
        "draft_reply": f"{open_line} {bridge} {closing}",
        "include_bridging": True,
    }


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
    api_key = get_ai_api_key()
    model = get_ai_model()
    base_url = get_ai_base_url()

    # Jika API key belum diisi, gunakan fallback personal berbasis sinyal teks.
    if not api_key:
        return fallback_draft(post_text, "kredensial AI belum diisi — draft personal dari pola teks, bukan template tunggal")

    # Dukungan Google Gemini API
    if "gemini" in model.lower() or api_key.startswith("AIzaSy"):
        gemini_model = model if "gemini" in model.lower() else "gemini-1.5-flash"
        url = base_url or f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={api_key}"
        headers = {
            "Content-Type": "application/json"
        }
        full_prompt = f"{SYSTEM_PROMPT}\n\nPostingan Threads:\n\"{post_text}\""
        payload = {
            "contents": [
                {
                    "parts": [{"text": full_prompt}]
                }
            ],
            "generationConfig": {
                # 0.85: variasi antar-draft — perintah user 2026-10-22 draft
                # jangan generik & sama semua.
                "temperature": 0.85,
                "responseMimeType": "application/json"
            }
        }
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=25)
            if res.status_code == 200:
                data = res.json()
                raw_content = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = extract_json_from_text(raw_content)
                if parsed:
                    return parsed
            else:
                logger.warning(f"Google Gemini HTTP {res.status_code}: {res.text}")
        except Exception as e:
            logger.warning(f"Panggilan Google Gemini error: {e}")

        # Jangan teruskan ke Anthropic/OpenAI jika kredensial adalah Google Gemini.
        return fallback_draft(post_text, f"panggilan Google Gemini ({gemini_model}) gagal/error — draft personal dari pola teks")

    # Dukungan Anthropic API
    if "claude" in model.lower() or api_key.startswith("sk-ant"):
        url = base_url or "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        payload = {
            "model": model,
            "max_tokens": 500,
            "temperature": 0.85,
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
    url = base_url or "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model if "claude" not in model.lower() else "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Postingan Threads:\n\"{post_text}\""}
        ],
        "temperature": 0.85
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


def _reasoning_with_ts_read(result: Dict[str, Any]) -> str:
    """Gabungkan bacaan TS (maksud/tujuan/emosi) ke reasoning agar terlihat reviewer."""
    base = str(result.get("reasoning", "Analisis relevansi kajian sunnah"))
    ts = result.get("ts_read") or {}
    parts = []
    if ts.get("maksud"):
        parts.append(f"Maksud: {ts['maksud']}")
    if ts.get("tujuan"):
        parts.append(f"Tujuan: {ts['tujuan']}")
    if ts.get("emosi"):
        parts.append(f"Emosi: {ts['emosi']}")
    if parts:
        return " · ".join(parts) + f" — {base}"
    return base


def process_candidates(db_path: Optional[str] = None) -> int:
    candidates = get_candidates_without_draft(db_path or DB_PATH)
    processed_count = 0

    for cand in candidates:
        try:
            result = call_ai_api(cand["text"])
        except Exception as e:
            logger.error(f"Gagal memproses post {cand['post_id']}: {e}")
            result = fallback_draft(cand["text"], f"panggilan AI error ({str(e)[:60]}) — draft personal dari pola teks")

        try:
            save_draft(
                post_id=cand["post_id"],
                is_relevant=bool(result.get("is_relevant", True)),
                ai_reasoning=_reasoning_with_ts_read(result),
                draft_reply=str(result.get("draft_reply") or fallback_draft(cand["text"], "AI mengembalikan draft kosong")["draft_reply"]),
                include_bridging=bool(result.get("include_bridging", True)),
                db_path=db_path or DB_PATH
            )
            processed_count += 1
        except Exception as e:
            logger.error(f"Gagal menyimpan draf post {cand['post_id']}: {e}")

    return processed_count


if __name__ == "__main__":
    count = process_candidates()
    print(f"Selesai menghasilkan {count} draft balasan.")

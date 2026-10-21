"""Mesin penyusun draft balasan — LOKAL, tanpa AI eksternal, tanpa kredensial.

ROMBAK 2026-10-22 (perintah user): seluruh jalur AI eksternal (Gemini/
Anthropic/OpenAI) dan kredensialnya DIHAPUS dari app ini. Draft disusun
oleh mesin lokal berbasis sinyal teks postingan TS:

1. BACA TS: petakan maksud (kota yang dicari, khusus akhwat/pemula/anak,
   cari teman berangkat) dan emosi/nada (antusias, ragu/malu, santai-gaul,
   netral) dari kata-kata di postingannya sendiri — tidak mengarang konteks.
2. SUSUN draft yang selaras bacaan itu: kalimat empati sesuai sinyal
   terkuatnya, bridging Majelisku.web.id menyebut kota TS, penutup bervariasi.
   Varian dipilih deterministik dari hash teks — postingan yang sama selalu
   mendapat draft yang sama, antar-postingan berbeda.
3. Reasoning yang tersimpan JUJUR menyebut bacaan sinyalnya agar reviewer
   bisa menilai sekilas sebelum menyetujui.
"""
import hashlib
import logging
from typing import Dict, Any, Optional

from config import DB_PATH
from db import get_candidates_without_draft, save_draft

logger = logging.getLogger(__name__)

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
    """Baca sinyal maksud/kondisi/emosi dari teks TS."""
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


def compose_draft(post_text: str) -> Dict[str, Any]:
    """Susun draft personal dari sinyal teks — bukan satu template untuk semua."""
    sig = read_ts_signals(post_text)
    top = sig["signals"][0] if sig["signals"] else "default"
    open_line = _pick(_EMPATHY_OPEN.get(top, _EMPATHY_OPEN["default"]), post_text, "open")
    tempat = f"di sekitar {sig['city']}" if sig["city"] else "di berbagai kota"
    bridge = _pick(_BRIDGE, post_text, "bridge").format(tempat=tempat)
    closing = _pick(_CLOSING, post_text, "closing")
    return {
        "is_relevant": True,
        "reasoning": (
            f"Mesin lokal — draft disusun dari sinyal teks postingan "
            f"(maksud: {sig['maksud']}; emosi: {sig['emosi']}). "
            f"Tinjau isi & sumbernya sebelum membalas."
        ),
        "draft_reply": f"{open_line} {bridge} {closing}",
        "include_bridging": True,
    }


def process_candidates(db_path: Optional[str] = None) -> int:
    candidates = get_candidates_without_draft(db_path or DB_PATH)
    processed_count = 0

    for cand in candidates:
        result = compose_draft(cand["text"])
        try:
            save_draft(
                post_id=cand["post_id"],
                is_relevant=bool(result.get("is_relevant", True)),
                ai_reasoning=str(result.get("reasoning", "")),
                draft_reply=str(result.get("draft_reply") or ""),
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

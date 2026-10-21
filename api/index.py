import hashlib
import json
import os
import sys
import uuid
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Pastikan root directory terbaca dalam Python path
CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import (
    get_threads_token,
    get_threads_user_id,
    get_threads_api_base_url,
    get_ai_api_key,
    get_ai_model,
    get_ai_base_url,
    get_score_threshold,
    save_app_settings
)
from db import (
    init_db,
    save_raw_posts,
    get_review_queue,
    get_approved_queue,
    get_skipped_queue,
    reset_draft_to_pending,
    reset_all_drafts_to_pending,
    wipe_all_database_posts,
    get_candidates_without_draft,
    update_draft_review,
    get_stats,
    get_setting,
    set_setting,
    get_all_raw_posts,
    force_candidate_and_draft,
    reclassify_all_raw_posts
)
from fetch_threads import run_fetch
from classifier import classify_pending_posts
from generate_draft import process_candidates
from credentials_checker import validate_threads_credentials, validate_ai_credentials

# Inisialisasi basis data
init_db()

# --- Seed leads hasil scrape eksternal (dimuat sekali saat DB masih kosong) ---
def _load_seed_leads() -> None:
    # AUDIT 2026-10-22: seed = leads ASLI hasil scrape (data/leads_seed.json),
    # dimuat SEKALI. Flag settings 'seed_v1_done' memastikan seed tidak
    # bangkit lagi setelah admin mengosongkan database.
    try:
        if get_setting("seed_v1_done"):
            return
        if get_stats().get("total_raw", 0) > 0:
            set_setting("seed_v1_done", "1")
            return
        seed_path = ROOT_DIR / "data" / "leads_seed.json"
        if not seed_path.exists():
            return
        raw = json.loads(seed_path.read_text(encoding="utf-8"))
        cleaned = []
        for p in raw:
            if not p.get("text"):
                continue
            cleaned.append({
                "post_id": str(p.get("post_id") or f"seed_{uuid.uuid4().hex[:10]}"),
                "author": str(p.get("author", "")).lstrip("@"),
                "text": str(p["text"]),
                "url": str(p.get("url", "")),
                "created_at": str(p.get("created_at") or datetime.now(timezone.utc).isoformat()),
            })
        if cleaned:
            save_raw_posts(cleaned)
            classify_pending_posts()
            process_candidates()
            set_setting("seed_v1_done", "1")
    except Exception:
        pass

_load_seed_leads()

app = FastAPI(title="Majelisku Social Assist")

@app.middleware("http")
async def auto_hydrate_settings_middleware(request: Request, call_next):
    # Auto-hydrate basis data container serverless jika klien mengirimkan kredensial di header
    threads_h = request.headers.get("x-threads-token")
    threads_uid_h = request.headers.get("x-threads-user-id")
    ai_key_h = request.headers.get("x-ai-key")
    ai_model_h = request.headers.get("x-ai-model")
    ai_base_url_h = request.headers.get("x-ai-base-url")

    updates = {}
    if threads_h and threads_h.strip() != get_threads_token():
        updates["THREADS_ACCESS_TOKEN"] = threads_h.strip()
    if threads_uid_h and threads_uid_h.strip() != get_threads_user_id():
        updates["THREADS_USER_ID"] = threads_uid_h.strip()
    if ai_key_h and ai_key_h.strip() != get_ai_api_key():
        updates["AI_API_KEY"] = ai_key_h.strip()
    if ai_model_h and ai_model_h.strip() != (get_setting("AI_MODEL") or ""):
        updates["AI_MODEL"] = ai_model_h.strip()
    if ai_base_url_h and ai_base_url_h.strip() != get_ai_base_url():
        updates["AI_BASE_URL"] = ai_base_url_h.strip()

    if updates:
        save_app_settings(updates)

    response = await call_next(request)
    return response

class ReviewRequest(BaseModel):
    post_id: str
    action: str  # "approve" | "skip" | "posted"
    final_reply: Optional[str] = ""

class SettingsRequest(BaseModel):
    threads_token: Optional[str] = ""
    threads_user_id: Optional[str] = "me"
    ai_api_key: Optional[str] = ""
    ai_model: Optional[str] = "claude-3-5-sonnet-20241022"
    ai_base_url: Optional[str] = ""
    score_threshold: Optional[int] = 2

class ValidateRequest(BaseModel):
    target: str  # "threads" | "ai"
    threads_token: Optional[str] = ""
    threads_user_id: Optional[str] = "me"
    ai_api_key: Optional[str] = ""
    ai_model: Optional[str] = "gemini-1.5-flash"
    ai_base_url: Optional[str] = ""

class AddThreadRequest(BaseModel):
    author: Optional[str] = "threads_user"
    text: str
    url: Optional[str] = ""

class ResetQueueRequest(BaseModel):
    post_id: Optional[str] = None

@app.get("/api/stats")
def api_stats():
    res = get_stats()
    res["threads_configured"] = bool(get_threads_token())
    res["ai_configured"] = bool(get_ai_api_key())
    res["ai_model"] = get_ai_model()
    return res

@app.get("/api/queue")
def api_queue():
    queue = get_review_queue()
    if not queue:
        # Jika antrean review kosong tapi ada kandidat belum didraf, otomatis proses draftnya
        unprocessed = get_candidates_without_draft()
        if unprocessed:
            process_candidates()
            queue = get_review_queue()
    return queue

@app.get("/api/approved")
def api_approved():
    return get_approved_queue()

@app.get("/api/skipped")
def api_skipped():
    return get_skipped_queue()

@app.get("/api/raw-posts")
def api_raw_posts():
    return get_all_raw_posts()

class ForceCandidateRequest(BaseModel):
    post_id: str

@app.post("/api/force-candidate")
def api_force_candidate(payload: ForceCandidateRequest):
    if not payload.post_id:
        raise HTTPException(status_code=400, detail="post_id wajib diisi")
    force_candidate_and_draft(payload.post_id)
    return {"status": "ok", "stats": get_stats()}

class ReclassifyRequest(BaseModel):
    threshold: Optional[int] = 2

@app.post("/api/reclassify")
def api_reclassify(payload: Optional[ReclassifyRequest] = None):
    th = payload.threshold if payload and payload.threshold is not None else 2
    count = reclassify_all_raw_posts(new_threshold=th)
    return {"status": "ok", "reclassified": count, "stats": get_stats()}

@app.post("/api/review")
def api_review(req: ReviewRequest):
    if req.action == "approve":
        update_draft_review(
            post_id=req.post_id,
            review_status="approved",
            final_reply=req.final_reply.strip() if req.final_reply else ""
        )
    elif req.action == "skip":
        update_draft_review(
            post_id=req.post_id,
            review_status="skipped",
            final_reply=""
        )
    elif req.action == "posted":
        update_draft_review(
            post_id=req.post_id,
            review_status="posted",
            final_reply=req.final_reply
        )
    else:
        raise HTTPException(status_code=400, detail="Aksi tidak dikenal")
    return {"status": "ok", "stats": get_stats()}

@app.post("/api/reset-queue")
def api_reset_queue(req: Optional[ResetQueueRequest] = None):
    post_id = req.post_id if req else None
    if post_id:
        reset_draft_to_pending(post_id)
    else:
        reset_all_drafts_to_pending()
    return {"status": "ok", "stats": get_stats()}

@app.post("/api/wipe-database")
def api_wipe_database():
    wipe_all_database_posts()
    return {
        "status": "ok",
        "message": "Seluruh data post dan draf di database berhasil direset bersih.",
        "stats": get_stats()
    }

@app.post("/api/trigger-meta-test-calls")
def api_trigger_meta_test_calls():
    token = get_threads_token()
    if not token:
        return {
            "status": "error",
            "message": "Token akses Threads belum diisi di Pengaturan."
        }

    results = {}

    # 1. Panggilan threads_basic: me info
    try:
        r1 = requests.get(
            f"{get_threads_api_base_url()}/me",
            params={"fields": "id,username", "access_token": token},
            timeout=10
        )
        results["threads_basic_me"] = {
            "status_code": r1.status_code,
            "response": r1.json() if r1.status_code == 200 else r1.text[:200]
        }
    except Exception as e:
        results["threads_basic_me"] = {"error": str(e)}

    # 2. Panggilan threads_basic: me/threads
    try:
        r2 = requests.get(
            f"{get_threads_api_base_url()}/me/threads",
            params={"fields": "id,text", "access_token": token},
            timeout=10
        )
        results["threads_basic_posts"] = {
            "status_code": r2.status_code,
            "response": r2.json() if r2.status_code == 200 else r2.text[:200]
        }
    except Exception as e:
        results["threads_basic_posts"] = {"error": str(e)}

    # 3. Panggilan threads_keyword_search: keyword_search (endpoint resmi Meta)
    try:
        r3 = requests.get(
            f"{get_threads_api_base_url()}/keyword_search",
            params={
                "q": "kajian",
                "search_type": "RECENT",
                "fields": "id,text,permalink,timestamp,username",
                "access_token": token
            },
            timeout=10
        )
        results["threads_keyword_search"] = {
            "status_code": r3.status_code,
            "response": r3.json() if r3.status_code == 200 else r3.text[:200]
        }
    except Exception as e:
        results["threads_keyword_search"] = {"error": str(e)}

    return {
        "status": "ok",
        "message": "Panggilan uji Meta Threads API selesai dikirim.",
        "results": results
    }

@app.post("/api/add-thread")
def api_add_thread(req: AddThreadRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Teks thread wajib diisi.")
    post_id = f"custom_{uuid.uuid4().hex[:8]}"
    author = req.author.strip().lstrip("@") or "threads_user"
    now_iso = datetime.now(timezone.utc).isoformat()
    post = [{
        "post_id": post_id,
        "author": author,
        "text": req.text.strip(),
        "url": req.url.strip() or f"https://www.threads.net/@{author}/post/{post_id}",
        "created_at": now_iso
    }]
    save_raw_posts(post)
    classify_pending_posts()
    process_candidates()
    return {"status": "ok", "post_id": post_id, "stats": get_stats()}

class ImportPostItem(BaseModel):
    post_id: Optional[str] = ""
    author: Optional[str] = ""
    text: str
    url: Optional[str] = ""
    created_at: Optional[str] = ""

class ImportPostsRequest(BaseModel):
    posts: List[ImportPostItem]

@app.post("/api/import-posts")
def api_import_posts(req: ImportPostsRequest):
    """Import hasil scrape eksternal (mis. sapuan sosial Muse) ke pipeline.

    Setiap postingan langsung melewati tahap 2 (saring + draft) secara otomatis,
    sehingga hasil import yang lolos filter langsung muncul di Antrean Review.
    """
    cleaned = []
    for p in req.posts:
        text = (p.text or "").strip()
        if not text:
            continue
        pid = (p.post_id or "").strip()
        if not pid:
            pid = "import_" + hashlib.sha256(((p.url or "") + "|" + text).encode("utf-8")).hexdigest()[:12]
        cleaned.append({
            "post_id": pid,
            "author": (p.author or "").strip().lstrip("@"),
            "text": text,
            "url": (p.url or "").strip(),
            "created_at": (p.created_at or "").strip() or datetime.now(timezone.utc).isoformat(),
        })
    if not cleaned:
        raise HTTPException(status_code=400, detail="Tidak ada postingan valid untuk diimport (field text wajib).")
    inserted = save_raw_posts(cleaned)
    classified = classify_pending_posts()
    drafted = process_candidates()
    return {
        "status": "ok",
        "received": len(cleaned),
        "inserted": inserted,
        "classified": classified,
        "drafted": drafted,
        "stats": get_stats(),
    }



@app.post("/api/pipeline")
def api_pipeline():
    new_posts = run_fetch()
    classified = classify_pending_posts()
    drafted = process_candidates()
    res_stats = get_stats()
    res_stats["threads_configured"] = bool(get_threads_token())
    res_stats["ai_configured"] = bool(get_ai_api_key())
    res_stats["ai_model"] = get_ai_model()
    return {
        "status": "ok",
        "new_posts": new_posts,
        "classified": classified,
        "drafted": drafted,
        "stats": res_stats
    }

@app.get("/api/settings")
def api_get_settings():
    return {
        "threads_token": get_threads_token(),
        "threads_user_id": get_threads_user_id(),
        "ai_api_key": get_ai_api_key(),
        "ai_model": get_ai_model(),
        "ai_base_url": get_ai_base_url(),
        "score_threshold": get_score_threshold()
    }

@app.post("/api/settings")
def api_save_settings(req: SettingsRequest):
    save_app_settings({
        "THREADS_ACCESS_TOKEN": req.threads_token.strip() if req.threads_token else "",
        "THREADS_USER_ID": req.threads_user_id.strip() if req.threads_user_id else "me",
        "AI_API_KEY": req.ai_api_key.strip() if req.ai_api_key else "",
        "AI_MODEL": req.ai_model.strip() if req.ai_model else "gemini-1.5-flash",
        "AI_BASE_URL": req.ai_base_url.strip() if req.ai_base_url else "",
        "SCORE_THRESHOLD": str(req.score_threshold or 3)
    })
    return {
        "status": "ok",
        "message": "Pengaturan kredensial berhasil disimpan dan langsung aktif.",
        "settings": {
            "threads_token": get_threads_token(),
            "threads_user_id": get_threads_user_id(),
            "ai_api_key": get_ai_api_key(),
            "ai_model": get_ai_model(),
            "ai_base_url": get_ai_base_url(),
            "score_threshold": get_score_threshold()
        }
    }

@app.post("/api/validate-key")
def api_validate_key(req: ValidateRequest):
    if req.target == "threads":
        return validate_threads_credentials(
            token=req.threads_token or "",
            user_id=req.threads_user_id or "me"
        )
    elif req.target == "ai":
        return validate_ai_credentials(
            api_key=req.ai_api_key or "",
            model=req.ai_model or "gemini-1.5-flash",
            base_url=req.ai_base_url or ""
        )
    else:
        raise HTTPException(status_code=400, detail="Target validasi tidak valid.")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Majelisku Social Assist</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    </style>
</head>
<body class="bg-slate-50 text-slate-800 min-h-screen">
    <!-- Navbar / Header -->
    <header class="bg-white border-b border-slate-200 sticky top-0 z-20">
        <div class="max-w-6xl mx-auto px-4 py-3 flex flex-wrap items-center justify-between gap-3">
            <div class="flex items-center gap-3">
                <span class="text-2xl">🕌</span>
                <div>
                    <h1 class="text-lg font-bold text-slate-900 leading-tight">Majelisku Social Assist</h1>
                    <p class="text-xs text-slate-500">Asisten kurasi dan balasan thread kajian sunnah</p>
                </div>
            </div>
            <div class="flex flex-wrap items-center gap-2">
                <span id="status-pill-threads" class="text-xs px-2.5 py-1 rounded border font-medium bg-slate-100 text-slate-600 border-slate-200">
                    Threads: Memeriksa...
                </span>
                <span id="status-pill-ai" class="text-xs px-2.5 py-1 rounded border font-medium bg-slate-100 text-slate-600 border-slate-200">
                    AI: Memeriksa...
                </span>
                                <button id="btn-pipeline" onclick="runPipeline()" class="inline-flex items-center gap-2 bg-emerald-700 hover:bg-emerald-800 text-white text-sm font-medium px-4 py-2 rounded-md transition shadow-sm">
                    <span id="pipeline-icon">🔄</span>
                    <span id="pipeline-text">Jalankan Pipeline</span>
                </button>
            </div>
        </div>
    </header>

    <!-- Main Container -->
    <main class="max-w-6xl mx-auto px-4 py-6">
        <!-- Status Bar & Metrics -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
            <div onclick="switchTab('raw')" class="bg-white border border-slate-200 hover:border-emerald-500 p-4 rounded-lg cursor-pointer transition shadow-xs group">
                <div class="flex items-center justify-between">
                    <div class="text-xs text-slate-500 font-medium group-hover:text-emerald-700">Total Post Terambil</div>
                    <span class="text-xs text-emerald-600 font-medium opacity-0 group-hover:opacity-100 transition">Lihat &rarr;</span>
                </div>
                <div id="stat-total" class="text-xl font-bold text-slate-900 mt-1">0</div>
            </div>
            <div class="bg-white border border-slate-200 p-4 rounded-lg">
                <div class="text-xs text-slate-500 font-medium">Lolos Filter (Kandidat)</div>
                <div id="stat-candidates" class="text-xl font-bold text-slate-900 mt-1">0</div>
            </div>
            <div class="bg-white border border-emerald-200 bg-emerald-50/50 p-4 rounded-lg">
                <div class="text-xs text-emerald-800 font-medium">Perlu Review</div>
                <div id="stat-pending" class="text-xl font-bold text-emerald-900 mt-1">0</div>
            </div>
            <div class="bg-white border border-slate-200 p-4 rounded-lg">
                <div class="text-xs text-slate-500 font-medium">Disetujui & Siap Posting</div>
                <div id="stat-approved" class="text-xl font-bold text-slate-900 mt-1">0</div>
            </div>
        </div>

        <!-- Pipeline 3 Langkah -->
        <div class="bg-white border border-emerald-200 rounded-lg p-4 sm:p-5 mb-6 shadow-xs">
            <div class="flex items-center gap-2 mb-4">
                <span class="text-base">🚀</span>
                <h2 class="text-sm font-bold text-slate-900">Pipeline — 3 Langkah Mudah</h2>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div class="border border-slate-200 rounded-lg p-3.5">
                    <div class="text-xs font-bold text-emerald-800 mb-1">LANGKAH 1 — AMBIL</div>
                    <p class="text-xs text-slate-500 mb-3">Tarik dari Threads, atau tempel hasil scrape eksternal (format JSON: daftar {author, text, url, created_at}).</p>
                    <div class="flex flex-wrap gap-2 mb-3">
                        <button onclick="runPipeline()" class="text-xs font-semibold px-3 py-1.5 rounded-md bg-emerald-700 hover:bg-emerald-800 text-white transition">🔄 Tarik dari Threads</button>
                        
                    </div>
                    <textarea id="import-json" rows="4" class="w-full text-xs font-mono p-2 border border-slate-300 rounded-md focus:ring-2 focus:ring-emerald-600 focus:outline-none" placeholder='[{"author":"namaakun","text":"kajian sunnah di bandung dimana ya?","url":"https://www.threads.com/@akun/post/xxx","created_at":"2026-10-14T08:00:00+00:00"}]'></textarea>
                    <button id="btn-import" onclick="importScrape()" class="mt-2 w-full text-xs font-semibold px-3 py-2 rounded-md bg-emerald-600 hover:bg-emerald-700 text-white transition">⬆️ Import Hasil Scrape</button>
                </div>
                <div class="border border-slate-200 rounded-lg p-3.5">
                    <div class="text-xs font-bold text-emerald-800 mb-1">LANGKAH 2 — SARING &amp; DRAFT</div>
                    <p class="text-xs text-slate-500 mb-3">Berjalan <b>otomatis</b> setiap selesai Langkah 1. Postingan diskor, yang lolos jadi kandidat, lalu AI menyusun draft balasan bridging ke Majelisku.web.id.</p>
                    <div class="space-y-1.5 text-xs text-slate-600">
                        <div class="flex justify-between"><span>Total postingan masuk</span><b id="pipe-total" class="text-slate-900">0</b></div>
                        <div class="flex justify-between"><span>Lolos filter (kandidat)</span><b id="pipe-candidates" class="text-slate-900">0</b></div>
                        <div class="flex justify-between"><span>Draft menunggu review</span><b id="pipe-pending" class="text-emerald-800">0</b></div>
                    </div>
                </div>
                <div class="border border-slate-200 rounded-lg p-3.5">
                    <div class="text-xs font-bold text-emerald-800 mb-1">LANGKAH 3 — REVIEW &amp; BALAS</div>
                    <p class="text-xs text-slate-500 mb-3">Periksa draft di Antrean Review, edit bila perlu, setujui, lalu tempel balasannya ke postingan aslinya. Semua keputusan tetap di tangan manusia.</p>
                    <button onclick="switchTab('review')" class="text-xs font-semibold px-3 py-2 rounded-md bg-slate-900 hover:bg-slate-800 text-white transition w-full">📋 Buka Antrean Review</button>
                </div>
            </div>
        </div>

        <!-- Tabs Navigation -->
        <div class="border-b border-slate-200 mb-6 flex flex-wrap gap-4 sm:gap-6">
            <button id="tab-review-btn" onclick="switchTab('review')" class="py-3 px-1 border-b-2 border-emerald-700 font-semibold text-sm text-emerald-800 flex items-center gap-2">
                Antrean Review
                <span id="badge-review-count" class="bg-emerald-100 text-emerald-800 text-xs px-2 py-0.5 rounded-full font-bold">0</span>
            </button>
            <button id="tab-raw-btn" onclick="switchTab('raw')" class="py-3 px-1 border-b-2 border-transparent font-medium text-sm text-slate-600 hover:text-slate-900 flex items-center gap-2">
                Semua Post Terambil
                <span id="badge-raw-count" class="bg-slate-100 text-slate-700 text-xs px-2 py-0.5 rounded-full font-bold">0</span>
            </button>
            <button id="tab-approved-btn" onclick="switchTab('approved')" class="py-3 px-1 border-b-2 border-transparent font-medium text-sm text-slate-600 hover:text-slate-900 flex items-center gap-2">
                Siap Diposting
                <span id="badge-approved-count" class="bg-slate-100 text-slate-700 text-xs px-2 py-0.5 rounded-full font-bold">0</span>
            </button>
            <button id="tab-skipped-btn" onclick="switchTab('skipped')" class="py-3 px-1 border-b-2 border-transparent font-medium text-sm text-slate-600 hover:text-slate-900 flex items-center gap-2">
                Dilewati (Skipped)
                <span id="badge-skipped-count" class="bg-slate-100 text-slate-700 text-xs px-2 py-0.5 rounded-full font-bold">0</span>
            </button>
            <button id="tab-settings-btn" onclick="switchTab('settings')" class="py-3 px-1 border-b-2 border-transparent font-medium text-sm text-slate-600 hover:text-slate-900 flex items-center gap-1.5">
                <span>⚙️</span> Pengaturan & Kredensial
            </button>
        </div>

        <!-- Section 1: Antrean Review -->
        <div id="view-review" class="space-y-4">
            <div id="review-loading" class="text-center py-12 text-slate-500">Memuat data antrean...</div>
            <div id="review-empty" class="hidden text-center py-12 bg-white border border-dashed border-slate-300 rounded-lg p-6">
                <div class="text-3xl mb-2">📭</div>
                <h3 class="text-base font-bold text-slate-800">Antrean review saat ini sedang kosong</h3>
                <p class="text-xs text-slate-500 max-w-md mx-auto mt-1">
                    Semua thread kandidat telah selesai direview atau belum ada data baru. Klik tombol "Jalankan Pipeline" di navbar atas untuk menarik dan memproses postingan Threads terbaru.
                </p>
            </div>
            <div id="review-list" class="space-y-4"></div>
        </div>

        <!-- Section: Semua Post Terambil (Raw Posts) -->
        <div id="view-raw" class="hidden space-y-4">
            <div class="bg-white border border-slate-200 rounded-lg p-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h3 class="text-sm font-bold text-slate-900">Daftar Seluruh Postingan Terambil dari Threads</h3>
                    <p class="text-xs text-slate-500 mt-0.5">Postingan yang ditarik via Threads API beserta skor relevansi. Klik tombol aksi pada kartu untuk memasukkan postingan ke Antrean Review.</p>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    <button onclick="reclassifyAll(2)" class="text-xs px-3 py-1.5 bg-slate-800 hover:bg-slate-900 text-white rounded font-medium transition shadow-xs">
                        ⚡ Nilai Ulang (Ambang Batas 2)
                    </button>
                    <button onclick="reclassifyAll(1)" class="text-xs px-3 py-1.5 bg-emerald-700 hover:bg-emerald-800 text-white rounded font-medium transition shadow-xs">
                        ⚡ Masukkan Semua (Ambang Batas 1)
                    </button>
                </div>
            </div>
            <div id="raw-loading" class="text-center py-12 text-slate-500">Memuat seluruh post...</div>
            <div id="raw-empty" class="hidden text-center py-12 bg-white border border-dashed border-slate-300 rounded-lg p-6">
                <div class="text-3xl mb-2">📥</div>
                <h3 class="text-base font-bold text-slate-800">Belum ada post yang ditarik</h3>
                <p class="text-xs text-slate-500 max-w-md mx-auto mt-1">
                    Klik tombol "Jalankan Pipeline" di navbar atas untuk menarik postingan dari akun Threads Anda.
                </p>
            </div>
            <div id="raw-list" class="space-y-4"></div>
        </div>

        <!-- Section 2: Siap Diposting -->
        <div id="view-approved" class="hidden space-y-4">
            <div id="approved-empty" class="hidden text-center py-12 bg-white border border-dashed border-slate-300 rounded-lg">
                <p class="text-slate-700 font-medium">Belum ada draft yang disetujui.</p>
                <p class="text-sm text-slate-500 mt-1">Selesaikan review di tab Antrean Review terlebih dahulu.</p>
            </div>
            <div id="approved-list" class="space-y-4"></div>
        </div>

        <!-- Section 3: Dilewati (Skipped) -->
        <div id="view-skipped" class="hidden space-y-4">
            <div id="skipped-empty" class="hidden text-center py-12 bg-white border border-dashed border-slate-300 rounded-lg">
                <p class="text-slate-700 font-medium">Tidak ada post yang dilewati.</p>
                <p class="text-sm text-slate-500 mt-1">Post yang ditolak atau dilewati dari antrean review akan tercatat di sini.</p>
            </div>
            <div id="skipped-list" class="space-y-4"></div>
        </div>

        <!-- Section 4: Pengaturan & Kredensial -->
        <div id="view-settings" class="hidden space-y-6">
            <div class="bg-white border border-slate-200 rounded-lg p-6 shadow-sm">
                <div class="flex flex-wrap items-center justify-between pb-4 border-b border-slate-200 mb-6 gap-2">
                    <div>
                        <h2 class="text-base font-bold text-slate-900">Pengaturan Kredensial & Validasi API</h2>
                        <p class="text-xs text-slate-500 mt-0.5">Ubah dan uji langsung kredensial Threads API serta AI Anda dengan umpan balik status instan.</p>
                    </div>
                    <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-semibold bg-emerald-50 text-emerald-800 border border-emerald-200">
                        Penyimpanan Aman Aktif
                    </span>
                </div>

                <!-- Info Banner Persistensi -->
                <div class="mb-5 p-3.5 bg-emerald-50 border border-emerald-200 rounded-md text-xs text-emerald-900 flex items-start gap-2.5">
                    <span class="text-base">💾</span>
                    <div class="leading-relaxed">
                        <strong>Penyimpanan Browser Permanen:</strong> Kredensial Anda otomatis tersimpan di penyimpanan browser (LocalStorage) dan diinjeksi ke server setiap request. Kunci dan token Anda tidak akan hilang lagi saat halaman dimuat ulang atau saat server Vercel berganti container.
                    </div>
                </div>

                <form onsubmit="event.preventDefault(); saveSettings();" class="space-y-6">
                    <!-- Bagian Threads API -->
                    <div class="bg-slate-50/70 p-4 rounded-lg border border-slate-200">
                        <div class="flex flex-wrap items-center justify-between gap-2 mb-3">
                            <h3 class="text-sm font-bold text-slate-900 flex items-center gap-2">
                                <span class="text-base">🧵</span> Kredensial Meta Threads API
                            </h3>
                            <button type="button" id="btn-test-threads" onclick="testThreadsConnection()" class="text-xs font-semibold px-3 py-1.5 rounded border border-slate-300 bg-white hover:bg-slate-100 text-slate-700 transition flex items-center gap-1.5 shadow-sm">
                                <span>🔍</span> Uji Koneksi Threads
                            </button>
                        </div>

                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-xs font-semibold text-slate-700 mb-1">Threads Access Token</label>
                                <div class="relative">
                                    <input type="password" id="cfg-threads-token" oninput="onThreadsInput(this.value)" class="w-full text-sm font-mono p-2.5 pr-10 border border-slate-300 rounded-md bg-white focus:ring-2 focus:ring-emerald-600 focus:outline-none" placeholder="Masukkan token Graph API resmi">
                                    <button type="button" onclick="togglePasswordVisibility('cfg-threads-token')" class="absolute inset-y-0 right-0 px-3 flex items-center text-slate-400 hover:text-slate-600" title="Tampilkan/Sembunyikan">
                                        👁️
                                    </button>
                                </div>
                                <p id="hint-threads-token" class="text-xs text-slate-500 mt-1">Token resmi dari Meta Developers Dashboard (Threads Graph API).</p>
                            </div>

                            <div>
                                <label class="block text-xs font-semibold text-slate-700 mb-1">Threads User ID</label>
                                <input type="text" id="cfg-threads-user-id" class="w-full text-sm font-mono p-2.5 border border-slate-300 rounded-md bg-white focus:ring-2 focus:ring-emerald-600 focus:outline-none" placeholder="me (atau ID Threads)">
                                <p class="text-xs text-slate-500 mt-1">Gunakan 'me' untuk akun pemilik token atau isi user ID khusus.</p>
                            </div>
                        </div>

                        <!-- Feedback Card Threads -->
                        <div id="feedback-threads" class="hidden mt-3 p-3 rounded-md text-xs border transition"></div>
                    </div>

                    <!-- Bagian AI API -->
                    <div class="bg-slate-50/70 p-4 rounded-lg border border-slate-200">
                        <div class="flex flex-wrap items-center justify-between gap-2 mb-3">
                            <div class="flex items-center gap-2">
                                <h3 class="text-sm font-bold text-slate-900 flex items-center gap-2">
                                    <span class="text-base">🤖</span> Kredensial AI Model & Provider
                                </h3>
                                <span id="badge-ai-provider" class="hidden text-xs font-semibold px-2 py-0.5 rounded"></span>
                            </div>
                            <button type="button" id="btn-test-ai" onclick="testAiConnection()" class="text-xs font-semibold px-3 py-1.5 rounded border border-slate-300 bg-white hover:bg-slate-100 text-slate-700 transition flex items-center gap-1.5 shadow-sm">
                                <span>🔍</span> Uji Koneksi AI
                            </button>
                        </div>

                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-xs font-semibold text-slate-700 mb-1">AI API Key</label>
                                <div class="relative">
                                    <input type="password" id="cfg-ai-key" oninput="onAiInput(this.value)" class="w-full text-sm font-mono p-2.5 pr-10 border border-slate-300 rounded-md focus:ring-2 focus:ring-emerald-600 focus:outline-none" placeholder="AIzaSy... (Gemini) atau sk-ant-... (Claude) atau sk-...">
                                    <button type="button" onclick="togglePasswordVisibility('cfg-ai-key')" class="absolute inset-y-0 right-0 px-3 flex items-center text-slate-400 hover:text-slate-600" title="Tampilkan/Sembunyikan">
                                        👁️
                                    </button>
                                </div>
                                <p id="hint-ai-key" class="text-xs text-slate-500 mt-1">Dukung Google Gemini, Anthropic Claude, OpenAI, dan OpenRouter.</p>
                            </div>

                            <div>
                                <label class="block text-xs font-semibold text-slate-700 mb-1">Model AI</label>
                                <div class="space-y-2">
                                    <select id="cfg-ai-model-select" onchange="handleModelSelectChange()" class="w-full text-sm p-2.5 border border-slate-300 rounded-md bg-white focus:ring-2 focus:ring-emerald-600 focus:outline-none">
                                        <option value="gemini-1.5-flash">Google Gemini 1.5 Flash (gemini-1.5-flash - Rekomendasi Cepat & Hemat)</option>
                                        <option value="gemini-2.0-flash">Google Gemini 2.0 Flash (gemini-2.0-flash)</option>
                                        <option value="gemini-1.5-pro">Google Gemini 1.5 Pro (gemini-1.5-pro)</option>
                                        <option value="claude-3-5-sonnet-20241022">Claude 3.5 Sonnet (claude-3-5-sonnet-20241022)</option>
                                        <option value="claude-3-haiku-20240307">Claude 3 Haiku (claude-3-haiku-20240307)</option>
                                        <option value="gpt-4o-mini">OpenAI GPT-4o Mini (gpt-4o-mini)</option>
                                        <option value="deepseek/deepseek-chat">DeepSeek Chat (via OpenRouter)</option>
                                        <option value="custom">Model Kustom Lainnya...</option>
                                    </select>
                                    <input type="text" id="cfg-ai-model-custom" class="hidden w-full text-sm p-2.5 border border-slate-300 rounded-md focus:ring-2 focus:ring-emerald-600 focus:outline-none" placeholder="Contoh: gemini-1.5-flash">
                                </div>
                                <p class="text-xs text-slate-500 mt-1">Pilih model untuk validasi relevansi dan drafting balasan.</p>
                            </div>

                            <div class="md:col-span-2">
                                <label class="block text-xs font-semibold text-slate-700 mb-1">Base URL AI (Opsional)</label>
                                <input type="text" id="cfg-ai-base-url" class="w-full text-sm font-mono p-2.5 border border-slate-300 rounded-md focus:ring-2 focus:ring-emerald-600 focus:outline-none" placeholder="https://openrouter.ai/api/v1 (kosongkan jika default)">
                                <p class="text-xs text-slate-500 mt-1">Hanya diisi jika menggunakan reverse proxy atau provider pihak ketiga seperti OpenRouter.</p>
                            </div>
                        </div>

                        <!-- Feedback Card AI -->
                        <div id="feedback-ai" class="hidden mt-3 p-3 rounded-md text-xs border transition"></div>
                    </div>

                    <!-- Bagian Filter Scoring -->
                    <div class="bg-slate-50/70 p-4 rounded-lg border border-slate-200">
                        <h3 class="text-sm font-bold text-slate-900 mb-3 flex items-center gap-2">
                            <span class="text-base">⚖️</span> Parameter Filter & Scoring
                        </h3>
                        <div class="max-w-xs">
                            <label class="block text-xs font-semibold text-slate-700 mb-1">Ambang Batas Skor Relevansi</label>
                            <input type="number" id="cfg-score-threshold" min="1" max="10" value="3" class="w-full text-sm p-2.5 border border-slate-300 rounded-md bg-white focus:ring-2 focus:ring-emerald-600 focus:outline-none">
                            <p class="text-xs text-slate-500 mt-1">Nilai minimum agar post diproses ke tahap review AI (Default: 3).</p>
                        </div>
                    </div>

                    <div class="pt-2 flex items-center gap-3">
                        <button type="submit" id="btn-save-settings" class="bg-emerald-700 hover:bg-emerald-800 text-white font-medium text-sm px-5 py-2.5 rounded-md transition shadow-sm">
                            Simpan Pengaturan
                        </button>
                        <button type="button" onclick="loadSettings()" class="border border-slate-300 hover:bg-slate-100 text-slate-700 font-medium text-sm px-4 py-2.5 rounded-md transition">
                            Muat Ulang
                        </button>
                    </div>
                </form>
            </div>

            <!-- Panduan & Pengujian Meta Developers API Test -->
            <div class="bg-indigo-50/70 border border-indigo-200 rounded-lg p-5">
                <div class="flex flex-col md:flex-row md:items-center justify-between gap-3">
                    <div>
                        <h3 class="font-bold text-indigo-950 flex items-center gap-2">
                            <span>🚀</span> Panggilan Uji Izin Meta Developers (threads_basic & keyword_search)
                        </h3>
                        <p class="text-xs text-indigo-800 mt-1">
                            Meta mewajibkan minimal <strong>1 panggilan API uji coba</strong> per izin agar status di dasbor Meta berganti dari "0 dari 1 panggilan API diperlukan" menjadi terverifikasi.
                        </p>
                    </div>
                    <button type="button" onclick="triggerMetaTestCalls()" class="inline-flex items-center gap-1.5 text-xs font-semibold px-4 py-2 rounded-md bg-indigo-600 hover:bg-indigo-700 text-white transition shadow-sm whitespace-nowrap">
                        <span>⚡</span> Jalankan Uji Coba Meta
                    </button>
                </div>
                <div id="meta-test-result" class="hidden mt-3 p-3 bg-white rounded border border-indigo-200 text-xs font-mono text-slate-800 overflow-x-auto"></div>
            </div>

            <!-- Zona Bahaya: Reset Total Database -->
            <div class="bg-rose-50/70 border border-rose-200 rounded-lg p-5">
                <div class="flex flex-col md:flex-row md:items-center justify-between gap-3">
                    <div>
                        <h3 class="font-bold text-rose-950 flex items-center gap-2">
                            <span>🗑️</span> Zona Bahaya: Reset & Hapus Seluruh Data Postingan
                        </h3>
                        <p class="text-xs text-rose-800 mt-1">
                            Tindakan ini akan mengosongkan tabel postingan mentah dan draf di basis data (semua statistik kembali ke 0). Kredensial API Anda tetap tersimpan aman.
                        </p>
                    </div>
                    <button type="button" onclick="wipeDatabase()" class="inline-flex items-center gap-1.5 text-xs font-semibold px-4 py-2 rounded-md bg-rose-600 hover:bg-rose-700 text-white transition shadow-sm whitespace-nowrap">
                        <span>⚠️</span> Hapus & Kosongkan Database
                    </button>
                </div>
            </div>

            <!-- Informasi Prinsip & Etika PRD -->
            <div class="bg-white border border-slate-200 rounded-lg p-5">
                <h3 class="font-bold text-slate-900 mb-2">Prinsip & Keamanan Etika (PRD Bagian 7)</h3>
                <ul class="text-sm text-slate-600 space-y-2 list-disc list-inside">
                    <li><strong>Human-in-the-loop:</strong> Tidak ada bot auto-posting. Semua balasan wajib disetujui dan disalin manual oleh manusia.</li>
                    <li><strong>Relevansi Ketat:</strong> Bridging ke Majelisku.web.id hanya disertakan jika penanya secara eksplisit mencari info kajian.</li>
                    <li><strong>Transparansi:</strong> Jawaban substantif dan tidak memanipulasi emosi pembaca.</li>
                </ul>
            </div>
        </div>
    </main>

    <!-- Toast Notification -->
    <div id="toast" class="fixed bottom-5 right-5 bg-slate-900 text-white px-4 py-2.5 rounded-md shadow-lg text-sm hidden transition z-50"></div>

    <script>
        let currentTab = 'review';
        const STORAGE_KEY = 'majelisku_settings_v1';

        function getLocalSettings() {
            try {
                const val = localStorage.getItem(STORAGE_KEY);
                return val ? JSON.parse(val) : null;
            } catch (e) {
                return null;
            }
        }

        function saveLocalSettings(data) {
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
            } catch (e) {
                console.error('Gagal menyimpan ke localStorage:', e);
            }
        }

        async function syncSettingsToServer(settings) {
            if (!settings) return;
            try {
                await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(settings)
                });
            } catch (e) {}
        }

        // Wrapper fetch yang otomatis menyertakan kredensial agar serverless container selalu terhidrasi
        async function authFetch(url, options = {}) {
            const local = getLocalSettings();
            options.headers = options.headers || {};
            if (local) {
                if (local.threads_token && !options.headers['X-Threads-Token']) {
                    options.headers['X-Threads-Token'] = local.threads_token;
                }
                if (local.threads_user_id && !options.headers['X-Threads-User-Id']) {
                    options.headers['X-Threads-User-Id'] = local.threads_user_id;
                }
                if (local.ai_api_key && !options.headers['X-Ai-Key']) {
                    options.headers['X-Ai-Key'] = local.ai_api_key;
                }
                if (local.ai_model && !options.headers['X-Ai-Model']) {
                    options.headers['X-Ai-Model'] = local.ai_model;
                }
                if (local.ai_base_url && !options.headers['X-Ai-Base-Url']) {
                    options.headers['X-Ai-Base-Url'] = local.ai_base_url;
                }
            }
            return fetch(url, options);
        }

        function showToast(msg) {
            const toast = document.getElementById('toast');
            toast.innerText = msg;
            toast.classList.remove('hidden');
            setTimeout(() => toast.classList.add('hidden'), 4000);
        }

        function switchTab(tab) {
            currentTab = tab;
            document.getElementById('view-review').classList.toggle('hidden', tab !== 'review');
            document.getElementById('view-raw').classList.toggle('hidden', tab !== 'raw');
            document.getElementById('view-approved').classList.toggle('hidden', tab !== 'approved');
            document.getElementById('view-skipped').classList.toggle('hidden', tab !== 'skipped');
            document.getElementById('view-settings').classList.toggle('hidden', tab !== 'settings');

            const btnRev = document.getElementById('tab-review-btn');
            const btnRaw = document.getElementById('tab-raw-btn');
            const btnApp = document.getElementById('tab-approved-btn');
            const btnSkip = document.getElementById('tab-skipped-btn');
            const btnSet = document.getElementById('tab-settings-btn');

            const activeClass = 'py-3 px-1 border-b-2 border-emerald-700 font-semibold text-sm text-emerald-800 flex items-center gap-2';
            const inactiveClass = 'py-3 px-1 border-b-2 border-transparent font-medium text-sm text-slate-600 hover:text-slate-900 flex items-center gap-2';

            btnRev.className = (tab === 'review') ? activeClass : inactiveClass;
            btnRaw.className = (tab === 'raw') ? activeClass : inactiveClass;
            btnApp.className = (tab === 'approved') ? activeClass : inactiveClass;
            btnSkip.className = (tab === 'skipped') ? activeClass : inactiveClass;
            btnSet.className = (tab === 'settings') ? activeClass.replace('gap-2', 'gap-1.5') : inactiveClass.replace('gap-2', 'gap-1.5');

            if (tab === 'review') loadQueue();
            if (tab === 'raw') loadRawPosts();
            if (tab === 'approved') loadApproved();
            if (tab === 'skipped') loadSkipped();
            if (tab === 'settings') loadSettings();
        }

        async function fetchStats() {
            try {
                const res = await authFetch('/api/stats');
                const data = await res.json();
                document.getElementById('stat-total').innerText = data.total_raw;
                document.getElementById('stat-candidates').innerText = data.total_candidates;
                document.getElementById('stat-pending').innerText = data.pending_review;
                document.getElementById('stat-approved').innerText = data.approved;
                document.getElementById('badge-review-count').innerText = data.pending_review;
                document.getElementById('badge-raw-count').innerText = data.total_raw;
                document.getElementById('badge-approved-count').innerText = data.approved;
                document.getElementById('badge-skipped-count').innerText = data.skipped || 0;
                const pipeTotal = document.getElementById('pipe-total');
                if (pipeTotal) pipeTotal.innerText = data.total_raw;
                const pipeCand = document.getElementById('pipe-candidates');
                if (pipeCand) pipeCand.innerText = data.total_candidates;
                const pipePend = document.getElementById('pipe-pending');
                if (pipePend) pipePend.innerText = data.pending_review;

                // Update Status Pills di Navbar
                const local = getLocalSettings();
                const threadsActive = data.threads_configured || Boolean(local?.threads_token);
                const aiActive = data.ai_configured || Boolean(local?.ai_api_key);

                const pThreads = document.getElementById('status-pill-threads');
                if (threadsActive) {
                    pThreads.className = 'text-xs px-2.5 py-1 rounded border font-medium bg-emerald-50 text-emerald-800 border-emerald-200';
                    pThreads.innerText = 'Threads: Aktif';
                } else {
                    pThreads.className = 'text-xs px-2.5 py-1 rounded border font-medium bg-amber-50 text-amber-800 border-amber-200';
                    pThreads.innerText = 'Threads: Belum Diisi';
                }

                const pAi = document.getElementById('status-pill-ai');
                if (aiActive) {
                    pAi.className = 'text-xs px-2.5 py-1 rounded border font-medium bg-emerald-50 text-emerald-800 border-emerald-200';
                    pAi.innerText = 'AI: ' + (local?.ai_model || data.ai_model || 'Aktif');
                } else {
                    pAi.className = 'text-xs px-2.5 py-1 rounded border font-medium bg-slate-100 text-slate-700 border-slate-200';
                    pAi.innerText = 'AI: Fallback Mode';
                }
            } catch (e) {
                console.error(e);
            }
        }

        function onThreadsInput(val) {
            const hint = document.getElementById('hint-threads-token');
            const trimmed = (val || '').trim();
            if (!trimmed) {
                hint.innerText = 'Belum diisi. Masukkan token jika ingin mengambil data langsung dari Threads.';
                hint.className = 'text-xs text-slate-500 mt-1';
            } else if (trimmed.length < 25) {
                hint.innerText = 'Panjang token saat ini: ' + trimmed.length + ' karakter (Token Meta biasanya > 30 karakter).';
                hint.className = 'text-xs text-amber-600 mt-1';
            } else {
                hint.innerText = 'Panjang token sesuai (' + trimmed.length + ' karakter). Klik "Uji Koneksi Threads" untuk memverifikasi keaktifan token.';
                hint.className = 'text-xs text-emerald-700 mt-1 font-medium';
            }
        }

        function onAiInput(val) {
            const hint = document.getElementById('hint-ai-key');
            const badge = document.getElementById('badge-ai-provider');
            const trimmed = (val || '').trim();

            if (!trimmed) {
                badge.classList.add('hidden');
                hint.innerText = 'Kunci API belum diisi. Sistem akan menggunakan mode fallback deterministik yang aman.';
                hint.className = 'text-xs text-slate-500 mt-1';
                return;
            }

            badge.classList.remove('hidden');
            if (trimmed.startsWith('AIzaSy') || trimmed.startsWith('AIza')) {
                badge.className = 'text-xs font-semibold px-2 py-0.5 rounded bg-blue-100 text-blue-800 border border-blue-200';
                badge.innerText = 'Format: Google Gemini Key';
                hint.innerText = 'Format API Key Google Gemini terdeteksi valid.';
                hint.className = 'text-xs text-blue-700 mt-1 font-medium';

                const sel = document.getElementById('cfg-ai-model-select');
                if (sel && (sel.value.includes('claude') || sel.value.includes('gpt'))) {
                    sel.value = 'gemini-1.5-flash';
                }
            } else if (trimmed.startsWith('sk-ant-')) {
                badge.className = 'text-xs font-semibold px-2 py-0.5 rounded bg-purple-100 text-purple-800 border border-purple-200';
                badge.innerText = 'Format: Anthropic Claude Key';
                hint.innerText = 'Format API Key Anthropic terdeteksi valid.';
                hint.className = 'text-xs text-purple-700 mt-1 font-medium';
            } else if (trimmed.startsWith('sk-or-')) {
                badge.className = 'text-xs font-semibold px-2 py-0.5 rounded bg-sky-100 text-sky-800 border border-sky-200';
                badge.innerText = 'Format: OpenRouter Key';
                hint.innerText = 'Format API Key OpenRouter terdeteksi valid.';
                hint.className = 'text-xs text-sky-700 mt-1 font-medium';
            } else if (trimmed.startsWith('sk-')) {
                badge.className = 'text-xs font-semibold px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 border border-emerald-200';
                badge.innerText = 'Format: OpenAI / Kompatibel';
                hint.innerText = 'Format API Key OpenAI / standar terdeteksi valid.';
                hint.className = 'text-xs text-emerald-700 mt-1 font-medium';
            } else {
                badge.className = 'text-xs font-semibold px-2 py-0.5 rounded bg-slate-100 text-slate-700 border border-slate-200';
                badge.innerText = 'Format: Kustom (' + trimmed.length + ' karakter)';
                hint.innerText = 'Kunci API kustom. Klik "Uji Koneksi AI" untuk memeriksa autentikasi ke server.';
                hint.className = 'text-xs text-slate-600 mt-1';
            }
        }

        async function testThreadsConnection() {
            const btn = document.getElementById('btn-test-threads');
            const fb = document.getElementById('feedback-threads');
            const token = document.getElementById('cfg-threads-token').value.trim();
            const userId = document.getElementById('cfg-threads-user-id').value.trim() || 'me';

            btn.disabled = true;
            btn.innerHTML = '<span>⏳</span> Menguji...';
            fb.classList.remove('hidden');
            fb.className = 'mt-3 p-3 rounded-md text-xs border bg-slate-100 border-slate-300 text-slate-700';
            fb.innerHTML = '<strong>Memeriksa koneksi:</strong> Mengirim permintaan uji ke Meta Threads Graph API...';

            try {
                const res = await authFetch('/api/validate-key', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        target: 'threads',
                        threads_token: token,
                        threads_user_id: userId
                    })
                });
                const data = await res.json();
                if (data.valid) {
                    fb.className = 'mt-3 p-3 rounded-md text-xs border bg-emerald-50 border-emerald-300 text-emerald-900';
                    fb.innerHTML = '<strong>Koneksi Sukses:</strong> ' + data.message;
                } else {
                    fb.className = 'mt-3 p-3 rounded-md text-xs border bg-red-50 border-red-300 text-red-900';
                    fb.innerHTML = '<strong>Validasi Gagal:</strong> ' + data.message;
                }
            } catch (e) {
                fb.className = 'mt-3 p-3 rounded-md text-xs border bg-red-50 border-red-300 text-red-900';
                fb.innerHTML = '<strong>Kesalahan Jaringan:</strong> Tidak dapat menghubungi server untuk validasi.';
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<span>🔍</span> Uji Koneksi Threads';
            }
        }

        async function testAiConnection() {
            const btn = document.getElementById('btn-test-ai');
            const fb = document.getElementById('feedback-ai');
            const apiKey = document.getElementById('cfg-ai-key').value.trim();
            const selectEl = document.getElementById('cfg-ai-model-select');
            const customEl = document.getElementById('cfg-ai-model-custom');
            const chosenModel = (selectEl.value === 'custom') ? customEl.value.trim() : selectEl.value;
            const baseUrl = document.getElementById('cfg-ai-base-url').value.trim();

            btn.disabled = true;
            btn.innerHTML = '<span>⏳</span> Menguji...';
            fb.classList.remove('hidden');
            fb.className = 'mt-3 p-3 rounded-md text-xs border bg-slate-100 border-slate-300 text-slate-700';
            fb.innerHTML = '<strong>Memeriksa autentikasi:</strong> Menghubungi endpoint model ' + chosenModel + '...';

            try {
                const res = await authFetch('/api/validate-key', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        target: 'ai',
                        ai_api_key: apiKey,
                        ai_model: chosenModel,
                        ai_base_url: baseUrl
                    })
                });
                const data = await res.json();
                if (data.valid) {
                    fb.className = 'mt-3 p-3 rounded-md text-xs border bg-emerald-50 border-emerald-300 text-emerald-900';
                    fb.innerHTML = '<strong>Autentikasi Sukses:</strong> ' + data.message;
                } else {
                    fb.className = 'mt-3 p-3 rounded-md text-xs border bg-red-50 border-red-300 text-red-900';
                    fb.innerHTML = '<strong>Validasi Gagal:</strong> ' + data.message;
                }
            } catch (e) {
                fb.className = 'mt-3 p-3 rounded-md text-xs border bg-red-50 border-red-300 text-red-900';
                fb.innerHTML = '<strong>Kesalahan Jaringan:</strong> Tidak dapat menghubungi server untuk validasi AI.';
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<span>🔍</span> Uji Koneksi AI';
            }
        }

        async function triggerMetaTestCalls() {
            showToast('Mengirimkan panggilan uji coba ke Meta Threads API...');
            try {
                const res = await authFetch('/api/trigger-meta-test-calls', { method: 'POST' });
                const data = await res.json();
                const box = document.getElementById('meta-test-result');
                if (box) {
                    box.classList.remove('hidden');
                    box.innerHTML = '<strong class="text-indigo-900 block mb-1">Hasil Panggilan API Meta:</strong>' +
                        '<pre class="text-xs whitespace-pre-wrap">' + JSON.stringify(data, null, 2) + '</pre>';
                }
                showToast(data.message || 'Panggilan uji Meta berhasil dikirim! Silakan periksa dasbor Meta.');
            } catch (e) {
                showToast('Gagal mengirim panggilan uji ke Meta');
            }
        }

        function applyModelSelection(modelName) {
            const selectEl = document.getElementById('cfg-ai-model-select');
            const customEl = document.getElementById('cfg-ai-model-custom');
            if (!selectEl) return;
            let found = false;
            for (let i = 0; i < selectEl.options.length; i++) {
                if (selectEl.options[i].value === modelName) {
                    selectEl.selectedIndex = i;
                    found = true;
                    break;
                }
            }
            if (!found) {
                selectEl.value = 'custom';
                if (customEl) {
                    customEl.value = modelName;
                    customEl.classList.remove('hidden');
                }
            } else if (customEl) {
                customEl.classList.add('hidden');
            }
        }

        async function loadSettings() {
            const tokenInput = document.getElementById('cfg-threads-token');
            const keyInput = document.getElementById('cfg-ai-key');
            const userIdInput = document.getElementById('cfg-threads-user-id');
            const baseUrlInput = document.getElementById('cfg-ai-base-url');
            const thresholdInput = document.getElementById('cfg-score-threshold');

            // 1. Muat data dari localStorage terlebih dahulu (PASTI ADA, tidak akan hilang karena reload)
            const local = getLocalSettings();
            if (local) {
                if (local.threads_token) tokenInput.value = local.threads_token;
                if (local.threads_user_id) userIdInput.value = local.threads_user_id;
                if (local.ai_api_key) keyInput.value = local.ai_api_key;
                if (local.ai_base_url) baseUrlInput.value = local.ai_base_url;
                if (local.score_threshold) thresholdInput.value = local.score_threshold;
                if (local.ai_model) applyModelSelection(local.ai_model);

                onThreadsInput(tokenInput.value);
                onAiInput(keyInput.value);

                // Sinkronkan ke server di latar belakang jika container baru menyala
                syncSettingsToServer(local);
            }

            // 2. Coba ambil dari server untuk melengkapi jika ada env vars server
            try {
                const res = await authFetch('/api/settings');
                const data = await res.json();

                // Hanya timpa nilai jika field di form masih kosong dan server mengembalikan data
                if (data.threads_token && !tokenInput.value) {
                    tokenInput.value = data.threads_token;
                }
                if (data.ai_api_key && !keyInput.value) {
                    keyInput.value = data.ai_api_key;
                }
                if (data.threads_user_id && !userIdInput.value) {
                    userIdInput.value = data.threads_user_id;
                }
                if (data.ai_model && !local?.ai_model) {
                    applyModelSelection(data.ai_model);
                }

                onThreadsInput(tokenInput.value);
                onAiInput(keyInput.value);
            } catch (e) {
                console.error(e);
            }
        }

        function handleModelSelectChange() {
            const selectEl = document.getElementById('cfg-ai-model-select');
            const customEl = document.getElementById('cfg-ai-model-custom');
            if (selectEl.value === 'custom') {
                customEl.classList.remove('hidden');
                customEl.focus();
            } else {
                customEl.classList.add('hidden');
            }
        }

        function togglePasswordVisibility(id) {
            const el = document.getElementById(id);
            if (el) {
                el.type = (el.type === 'password') ? 'text' : 'password';
            }
        }

        async function saveSettings() {
            const btn = document.getElementById('btn-save-settings');
            btn.disabled = true;
            btn.innerText = 'Menyimpan...';

            const selectEl = document.getElementById('cfg-ai-model-select');
            const customEl = document.getElementById('cfg-ai-model-custom');
            const chosenModel = (selectEl.value === 'custom') ? customEl.value.trim() : selectEl.value;
            const token = document.getElementById('cfg-threads-token').value.trim();
            const apiKey = document.getElementById('cfg-ai-key').value.trim();
            const userId = document.getElementById('cfg-threads-user-id').value.trim() || 'me';
            const baseUrl = document.getElementById('cfg-ai-base-url').value.trim();
            const threshold = parseInt(document.getElementById('cfg-score-threshold').value, 10) || 3;

            const payload = {
                threads_token: token,
                threads_user_id: userId,
                ai_api_key: apiKey,
                ai_model: chosenModel || 'gemini-1.5-flash',
                ai_base_url: baseUrl,
                score_threshold: threshold
            };

            // 1. Simpan ke browser localStorage terlebih dahulu (Permanen & Tidak bisa hilang)
            saveLocalSettings(payload);

            // 2. Simpan ke server
            try {
                const res = await authFetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                showToast(data.message || 'Pengaturan berhasil disimpan dan dikunci!');
                fetchStats();

                if (token) testThreadsConnection();
                if (apiKey) testAiConnection();
            } catch (e) {
                showToast('Kredensial disimpan permanen di browser!');
            } finally {
                btn.disabled = false;
                btn.innerText = 'Simpan Pengaturan';
            }
        }

        async function loadQueue() {
            document.getElementById('review-loading').classList.remove('hidden');
            document.getElementById('review-empty').classList.add('hidden');
            document.getElementById('review-list').innerHTML = '';

            try {
                const res = await authFetch('/api/queue');
                const items = await res.json();
                document.getElementById('review-loading').classList.add('hidden');

                if (items.length === 0) {
                    const emptyEl = document.getElementById('review-empty');
                    emptyEl.classList.remove('hidden');
                    const totalRaw = parseInt(document.getElementById('stat-total').innerText, 10) || 0;
                    if (totalRaw > 0) {
                        emptyEl.innerHTML = `
                            <div class="text-3xl mb-2">📬</div>
                            <h3 class="text-base font-bold text-slate-800">Antrean Review Belum Berisi Post</h3>
                            <p class="text-xs text-slate-600 max-w-md mx-auto mt-1 mb-4 leading-relaxed">
                                Terdapat <strong>${totalRaw} post</strong> yang sudah berhasil ditarik dari Threads. Namun sistem filter menilai skornya di bawah ambang batas relevansi, sehingga belum otomatis masuk ke antrean draf.
                            </p>
                            <div class="flex flex-wrap items-center justify-center gap-2">
                                <button onclick="switchTab('raw')" class="text-xs px-3.5 py-2 bg-slate-800 hover:bg-slate-900 text-white rounded font-medium transition shadow-xs">
                                    👁️ Buka Tab Semua Post Terambil (${totalRaw})
                                </button>
                                <button onclick="reclassifyAll(1)" class="text-xs px-3.5 py-2 bg-emerald-700 hover:bg-emerald-800 text-white rounded font-medium transition shadow-xs">
                                    ⚡ Masukkan Semua ke Antrean (Ambang Batas 1)
                                </button>
                                <button onclick="runPipeline()" class="text-xs px-3.5 py-2 bg-slate-700 hover:bg-slate-800 text-white rounded font-medium transition shadow-xs">
                                    🔄 Tarik dari Threads
                                </button>
                            </div>
                        `;
                    } else {
                        emptyEl.innerHTML = `
                            <div class="text-3xl mb-2">📬</div>
                            <h3 class="text-base font-bold text-slate-800">Tidak ada antrean review</h3>
                            <p class="text-xs text-slate-500 max-w-md mx-auto mt-1 mb-4">
                                Tidak ada postingan yang menunggu tinjauan saat ini.
                            </p>
                            <button onclick="runPipeline()" class="text-xs px-4 py-2 bg-emerald-700 hover:bg-emerald-800 text-white rounded font-medium transition shadow-xs">
                                🔄 Tarik dari Threads
                            </button>
                        `;
                    }
                    return;
                }

                const listEl = document.getElementById('review-list');
                items.forEach(item => {
                    const card = document.createElement('div');
                    card.className = 'bg-white border border-slate-200 rounded-lg p-5 shadow-sm';
                    card.innerHTML = `
                        <div class="flex flex-wrap items-center justify-between gap-2 mb-3">
                            <div class="flex items-center gap-2">
                                <span class="font-bold text-slate-900">@${item.author}</span>
                                <span class="text-xs text-slate-500">${(item.created_at || '').substring(0, 19).replace('T', ' ')}</span>
                            </div>
                            <div class="flex items-center gap-2">
                                <span class="bg-slate-100 text-slate-700 text-xs font-semibold px-2.5 py-1 rounded border border-slate-200">Skor: ${item.score}</span>
                                <span class="${item.is_relevant ? 'bg-emerald-50 text-emerald-800 border-emerald-200' : 'bg-red-50 text-red-800 border-red-200'} text-xs font-semibold px-2.5 py-1 rounded border">
                                    ${item.is_relevant ? 'Relevan' : 'Perlu Cek'}
                                </span>
                            </div>
                        </div>
                        <div class="text-slate-800 text-base mb-3 leading-relaxed">
                            "${item.text}"
                        </div>
                        <div class="text-xs text-slate-600 mb-3 bg-slate-50 p-2.5 rounded border border-slate-100">
                            <strong>Analisis AI:</strong> ${item.ai_reasoning || 'Belum dianalisis'}
                        </div>
                        <div class="mb-4">
                            <label class="block text-xs font-semibold text-slate-700 mb-1">Draft Balasan (dapat diedit):</label>
                            <textarea id="reply-${item.post_id}" class="w-full text-sm p-3 border border-slate-300 rounded-md focus:ring-2 focus:ring-emerald-600 focus:outline-none" rows="3">${item.draft_reply || ''}</textarea>
                        </div>
                        <div class="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-slate-100">
                            <a href="${item.url}" target="_blank" rel="noopener noreferrer" class="text-xs font-medium text-emerald-700 hover:underline">
                                Buka tautan thread asli &rarr;
                            </a>
                            <div class="flex items-center gap-2">
                                <button onclick="handleReview('${item.post_id}', 'skip')" class="text-xs px-3 py-2 border border-slate-300 rounded font-medium text-slate-600 hover:bg-slate-100 transition">
                                    Lewati (Skip)
                                </button>
                                <button onclick="handleReview('${item.post_id}', 'approve')" class="text-xs px-4 py-2 bg-emerald-700 hover:bg-emerald-800 text-white rounded font-medium transition shadow-sm">
                                    Setujui Draft
                                </button>
                            </div>
                        </div>
                    `;
                    listEl.appendChild(card);
                });
            } catch (e) {
                console.error(e);
                document.getElementById('review-loading').innerText = 'Gagal memuat antrean.';
            }
        }

        async function loadRawPosts() {
            const loading = document.getElementById('raw-loading');
            const empty = document.getElementById('raw-empty');
            const listEl = document.getElementById('raw-list');
            loading.classList.remove('hidden');
            empty.classList.add('hidden');
            listEl.innerHTML = '';

            try {
                const res = await authFetch('/api/raw-posts');
                const items = await res.json();
                loading.classList.add('hidden');

                if (items.length === 0) {
                    empty.classList.remove('hidden');
                    return;
                }

                items.forEach(item => {
                    const card = document.createElement('div');
                    card.className = 'bg-white border border-slate-200 rounded-lg p-5 shadow-xs space-y-3';

                    let statusBadge = '';
                    if (item.status === 'candidate') {
                        statusBadge = `<span class="px-2.5 py-1 bg-emerald-100 text-emerald-800 text-xs font-semibold rounded-full border border-emerald-200">✅ Lolos Kandidat (Skor: ${item.score})</span>`;
                    } else if (item.status === 'rejected') {
                        statusBadge = `<span class="px-2.5 py-1 bg-amber-100 text-amber-800 text-xs font-semibold rounded-full border border-amber-200">⚠️ Disaring Keluar (Skor: ${item.score})</span>`;
                    } else {
                        statusBadge = `<span class="px-2.5 py-1 bg-slate-100 text-slate-700 text-xs font-semibold rounded-full border border-slate-200">⏳ Pending (Skor: ${item.score})</span>`;
                    }

                    let reviewBadge = '';
                    if (item.review_status === 'approved') {
                        reviewBadge = `<span class="text-xs font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded">Disetujui</span>`;
                    } else if (item.review_status === 'skipped') {
                        reviewBadge = `<span class="text-xs font-semibold text-slate-500 bg-slate-100 px-2 py-0.5 rounded">Dilewati</span>`;
                    } else if (item.review_status === 'pending_review') {
                        reviewBadge = `<span class="text-xs font-semibold text-blue-700 bg-blue-50 px-2 py-0.5 rounded">Ada di Antrean Review</span>`;
                    }

                    let actionHtml = '';
                    if (item.review_status === 'pending_review') {
                        actionHtml = `<button onclick="switchTab('review')" class="text-xs px-3 py-1.5 bg-blue-50 hover:bg-blue-100 text-blue-700 font-medium rounded transition">Buka di Antrean Review &rarr;</button>`;
                    } else if (item.status === 'rejected' || !item.review_status) {
                        actionHtml = `<button onclick="forceCandidate('${item.post_id}')" class="text-xs px-3 py-1.5 bg-emerald-700 hover:bg-emerald-800 text-white font-medium rounded transition shadow-xs">➕ Masukkan ke Antrean Review & Buat Draft AI</button>`;
                    }

                    card.innerHTML = `
                        <div class="flex flex-wrap items-center justify-between gap-2">
                            <div class="flex items-center gap-2">
                                <span class="font-bold text-slate-800 text-sm">@${item.author}</span>
                                <span class="text-xs text-slate-400">${(item.created_at || '').substring(0, 19).replace('T', ' ')}</span>
                            </div>
                            <div class="flex items-center gap-2">
                                ${statusBadge}
                                ${reviewBadge}
                            </div>
                        </div>
                        <div class="text-sm text-slate-700 bg-slate-50 p-3.5 rounded-md border border-slate-100 leading-relaxed">
                            "${item.text}"
                        </div>
                        ${item.draft_reply ? `
                        <div class="text-xs bg-emerald-50/60 p-2.5 rounded border border-emerald-100 text-slate-700">
                            <strong>Draf AI:</strong> ${item.draft_reply}
                        </div>` : ''}
                        <div class="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-slate-100">
                            <a href="${item.url}" target="_blank" rel="noopener noreferrer" class="text-xs font-medium text-emerald-700 hover:underline">
                                Buka postingan asli di Threads &rarr;
                            </a>
                            <div>${actionHtml}</div>
                        </div>
                    `;
                    listEl.appendChild(card);
                });
            } catch (e) {
                console.error(e);
                loading.innerText = 'Gagal memuat daftar post terambil.';
            }
        }

        async function forceCandidate(postId) {
            showToast('Menjadikan kandidat dan meminta AI membuat draft balasan...');
            try {
                const res = await authFetch('/api/force-candidate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ post_id: postId })
                });
                const data = await res.json();
                showToast('Post berhasil dimasukkan ke Antrean Review dengan draf AI!');
                fetchStats();
                switchTab('review');
            } catch (e) {
                showToast('Gagal memproses post ke antrean review');
            }
        }

        async function reclassifyAll(threshold = 2) {
            showToast(`Menilai ulang seluruh post dengan ambang batas skor ${threshold}...`);
            try {
                const res = await authFetch('/api/reclassify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ threshold: threshold })
                });
                const data = await res.json();
                showToast(`Selesai: ${data.reclassified} post dinilai ulang dan draf AI disiapkan!`);
                fetchStats();
                switchTab('review');
            } catch (e) {
                showToast('Gagal menilai ulang post');
            }
        }

        async function loadApproved() {
            document.getElementById('approved-empty').classList.add('hidden');
            document.getElementById('approved-list').innerHTML = '';

            try {
                const res = await authFetch('/api/approved');
                const items = await res.json();

                if (items.length === 0) {
                    document.getElementById('approved-empty').classList.remove('hidden');
                    return;
                }

                const listEl = document.getElementById('approved-list');
                items.forEach(item => {
                    const card = document.createElement('div');
                    card.className = 'bg-white border border-slate-200 rounded-lg p-5 shadow-sm';
                    const replyText = item.final_reply || item.draft_reply || '';
                    card.innerHTML = `
                        <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
                            <span class="font-bold text-slate-900">Target: @${item.author}</span>
                            <span class="text-xs text-slate-500">Disetujui: ${(item.reviewed_at || '').substring(0, 19).replace('T', ' ')}</span>
                        </div>
                        <div class="text-sm text-slate-600 italic mb-3 bg-slate-50 p-2.5 rounded border border-slate-100">
                            "${item.text}"
                        </div>
                        <div class="mb-3">
                            <div class="text-xs font-semibold text-slate-700 mb-1">Teks Balasan Siap Salin:</div>
                            <div class="bg-slate-900 text-slate-100 p-3 rounded font-mono text-xs select-all break-words">
                                ${replyText}
                            </div>
                        </div>
                        <div class="flex flex-wrap items-center justify-between gap-3 pt-2">
                            <a href="${item.url}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-700 hover:underline">
                                Buka Threads untuk Membalas &rarr;
                            </a>
                            <div class="flex items-center gap-2">
                                <button onclick="resetSinglePost('${item.post_id}')" class="text-xs px-3 py-1.5 border border-slate-300 rounded font-medium text-slate-600 hover:bg-slate-100 transition">
                                    Kembalikan ke Antrean
                                </button>
                                <button onclick="copyToClipboard('${encodeURIComponent(replyText)}')" class="text-xs px-3 py-1.5 border border-slate-300 rounded font-medium text-slate-700 hover:bg-slate-100 transition">
                                    Salin Teks
                                </button>
                                <button onclick="handleReview('${item.post_id}', 'posted')" class="text-xs px-3 py-1.5 bg-slate-800 hover:bg-slate-900 text-white rounded font-medium transition">
                                    Tandai Selesai
                                </button>
                            </div>
                        </div>
                    `;
                    listEl.appendChild(card);
                });
            } catch (e) {
                console.error(e);
            }
        }

        async function loadSkipped() {
            document.getElementById('skipped-empty').classList.add('hidden');
            document.getElementById('skipped-list').innerHTML = '';

            try {
                const res = await authFetch('/api/skipped');
                const items = await res.json();

                if (items.length === 0) {
                    document.getElementById('skipped-empty').classList.remove('hidden');
                    return;
                }

                const listEl = document.getElementById('skipped-list');
                items.forEach(item => {
                    const card = document.createElement('div');
                    card.className = 'bg-white border border-slate-200 rounded-lg p-5 shadow-sm opacity-90';
                    card.innerHTML = `
                        <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
                            <span class="font-bold text-slate-700">@${item.author}</span>
                            <span class="text-xs text-slate-400">Dilewati: ${(item.reviewed_at || '').substring(0, 19).replace('T', ' ')}</span>
                        </div>
                        <div class="text-sm text-slate-600 mb-3">
                            "${item.text}"
                        </div>
                        <div class="text-xs text-slate-500 mb-3 bg-slate-50 p-2.5 rounded border border-slate-100">
                            <strong>Analisis AI:</strong> ${item.ai_reasoning || 'Dilewati oleh pengguna'}
                        </div>
                        <div class="flex items-center justify-between pt-2 border-t border-slate-100">
                            <a href="${item.url}" target="_blank" rel="noopener noreferrer" class="text-xs text-slate-500 hover:underline">
                                Lihat thread asli &rarr;
                            </a>
                            <button onclick="resetSinglePost('${item.post_id}')" class="text-xs px-3 py-1.5 bg-emerald-700 hover:bg-emerald-800 text-white rounded font-medium transition shadow-sm">
                                ↩️ Kembalikan ke Antrean Review
                            </button>
                        </div>
                    `;
                    listEl.appendChild(card);
                });
            } catch (e) {
                console.error(e);
            }
        }

        async function handleReview(postId, action) {
            const textarea = document.getElementById('reply-' + postId);
            const finalReply = textarea ? textarea.value : '';

            try {
                const res = await authFetch('/api/review', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ post_id: postId, action: action, final_reply: finalReply })
                });
                const data = await res.json();
                showToast(action === 'approve' ? 'Draft disetujui!' : (action === 'skip' ? 'Post dilewati.' : 'Ditandai selesai!'));
                fetchStats();
                if (currentTab === 'review') loadQueue();
                if (currentTab === 'approved') loadApproved();
                if (currentTab === 'skipped') loadSkipped();
            } catch (e) {
                showToast('Gagal memperbarui status');
            }
        }

        async function resetSinglePost(postId) {
            try {
                const res = await authFetch('/api/reset-queue', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ post_id: postId })
                });
                const data = await res.json();
                showToast('Post berhasil dikembalikan ke Antrean Review!');
                fetchStats();
                switchTab('review');
            } catch (e) {
                showToast('Gagal mengembalikan post ke antrean');
            }
        }

        async function resetAllQueue() {
            if (!confirm('Kembalikan semua post yang dilewati/disetujui kembali ke Antrean Review?')) return;
            try {
                const res = await authFetch('/api/reset-queue', { method: 'POST' });
                const data = await res.json();
                showToast('Semua post berhasil dikembalikan ke Antrean Review!');
                fetchStats();
                switchTab('review');
            } catch (e) {
                showToast('Gagal mereset antrean');
            }
        }


        async function wipeDatabase() {
            if (!confirm('PERINGATAN: Apakah Anda yakin ingin MENGHAPUS SEMUA data post dan draf di database? Seluruh metrik statistik akan kembali menjadi 0.')) return;
            try {
                const res = await authFetch('/api/wipe-database', { method: 'POST' });
                const data = await res.json();
                showToast(data.message || 'Seluruh data di database berhasil direset bersih!');
                fetchStats();
                loadQueue();
                switchTab('review');
            } catch (e) {
                showToast('Gagal mengosongkan database');
            }
        }


        function openAddModal() {
            document.getElementById('modal-add').classList.remove('hidden');
            document.getElementById('manual-text').focus();
        }

        function closeAddModal() {
            document.getElementById('modal-add').classList.add('hidden');
        }

        async function submitManualThread() {
            const author = document.getElementById('manual-author').value.trim();
            const text = document.getElementById('manual-text').value.trim();
            const url = document.getElementById('manual-url').value.trim();
            const btn = document.getElementById('btn-submit-manual');

            if (!text) {
                showToast('Teks postingan tidak boleh kosong.');
                return;
            }

            btn.disabled = true;
            btn.innerText = 'Memproses dengan AI...';

            try {
                const res = await authFetch('/api/add-thread', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ author: author, text: text, url: url })
                });
                const data = await res.json();
                closeAddModal();
                document.getElementById('manual-author').value = '';
                document.getElementById('manual-text').value = '';
                document.getElementById('manual-url').value = '';
                showToast('Thread berhasil ditambahkan dan masuk ke Antrean Review!');
                fetchStats();
                switchTab('review');
            } catch (e) {
                showToast('Gagal memproses thread manual');
            } finally {
                btn.disabled = false;
                btn.innerText = 'Proses dengan AI';
            }
        }


        async function runPipeline() {
            const btn = document.getElementById('btn-pipeline');
            const icon = document.getElementById('pipeline-icon');
            const text = document.getElementById('pipeline-text');

            btn.disabled = true;
            icon.innerText = '⏳';
            text.innerText = 'Memproses...';

            try {
                const res = await authFetch('/api/pipeline', { method: 'POST' });
                const data = await res.json();
                if (data.new_posts === 0 && data.stats && data.stats.review_queue === 0) {
                    showToast('Threads API: 0 post baru ditemukan. Pastikan token Threads valid.');
                } else if (data.new_posts === 0 && data.stats && data.stats.review_queue > 0) {
                    showToast(`Pipeline selesai: ${data.stats.review_queue} post siap ditinjau di antrean.`);
                } else {
                    showToast(`Selesai: ${data.new_posts} post ditarik, ${data.classified} disaring, ${data.drafted} draf dibuat.`);
                }
                fetchStats();
                switchTab('review');
            } catch (e) {
                showToast('Terjadi kesalahan saat menjalankan pipeline');
            } finally {
                btn.disabled = false;
                icon.innerText = '🔄';
                text.innerText = 'Jalankan Pipeline';
            }
        }

        async function importScrape() {
            const ta = document.getElementById('import-json');
            const btn = document.getElementById('btn-import');
            let parsed;
            try {
                parsed = JSON.parse(ta.value);
            } catch (e) {
                showToast('Format JSON tidak valid. Tempel daftar postingan dalam format JSON.');
                return;
            }
            const posts = Array.isArray(parsed) ? parsed : (parsed.posts || []);
            if (!posts.length) {
                showToast('Tidak ada postingan di dalam JSON.');
                return;
            }
            btn.disabled = true;
            btn.innerText = '⏳ Mengimport...';
            try {
                const res = await authFetch('/api/import-posts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ posts })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Import gagal');
                showToast(`Import selesai: ${data.inserted} baru masuk, ${data.classified} disaring, ${data.drafted} draft dibuat.`);
                ta.value = '';
                fetchStats();
                loadQueue();
                loadRawPosts();
                switchTab(data.drafted > 0 ? 'review' : 'raw');
            } catch (e) {
                showToast('Import gagal: ' + e.message);
            } finally {
                btn.disabled = false;
                btn.innerText = '⬆️ Import Hasil Scrape';
            }
        }

        function copyToClipboard(encodedText) {
            const text = decodeURIComponent(encodedText);
            navigator.clipboard.writeText(text).then(() => {
                showToast('Teks berhasil disalin ke clipboard!');
            }).catch(() => {
                showToast('Gagal menyalin teks');
            });
        }

        // Inisialisasi awal: Muat pengaturan dari localStorage terlebih dahulu, lalu muat data
        loadSettings().then(() => {
            fetchStats();
            loadQueue();
        });
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index_page():
    return HTML_TEMPLATE

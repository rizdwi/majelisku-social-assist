import os
import sys
from pathlib import Path

# Pastikan root directory terbaca dalam Python path
CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

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
    get_review_queue,
    get_approved_queue,
    update_draft_review,
    get_stats
)
from fetch_threads import run_fetch
from classifier import classify_pending_posts
from generate_draft import process_candidates
from credentials_checker import validate_threads_credentials, validate_ai_credentials

# Inisialisasi basis data
init_db()

app = FastAPI(title="Majelisku Social Assist")

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
    score_threshold: Optional[int] = 3

class ValidateRequest(BaseModel):
    target: str  # "threads" | "ai"
    threads_token: Optional[str] = ""
    threads_user_id: Optional[str] = "me"
    ai_api_key: Optional[str] = ""
    ai_model: Optional[str] = "claude-3-5-sonnet-20241022"
    ai_base_url: Optional[str] = ""

@app.get("/api/stats")
def api_stats():
    res = get_stats()
    res["threads_configured"] = bool(get_threads_token())
    res["ai_configured"] = bool(get_ai_api_key())
    res["ai_model"] = get_ai_model()
    return res

@app.get("/api/queue")
def api_queue():
    return get_review_queue()

@app.get("/api/approved")
def api_approved():
    return get_approved_queue()

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
        "THREADS_ACCESS_TOKEN": req.threads_token or "",
        "THREADS_USER_ID": req.threads_user_id or "me",
        "AI_API_KEY": req.ai_api_key or "",
        "AI_MODEL": req.ai_model or "claude-3-5-sonnet-20241022",
        "AI_BASE_URL": req.ai_base_url or "",
        "SCORE_THRESHOLD": str(req.score_threshold or 3)
    })
    return {
        "status": "ok",
        "message": "Pengaturan berhasil disimpan dan langsung aktif.",
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
            model=req.ai_model or "claude-3-5-sonnet-20241022",
            base_url=req.ai_base_url or ""
        )
    else:
        raise HTTPException(status_code=400, detail="Target validasi tidak dikenal")

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
                <button id="btn-pipeline" onclick="runPipeline()" class="inline-flex items-center gap-2 bg-emerald-700 hover:bg-emerald-800 text-white text-sm font-medium px-4 py-2 rounded-md transition shadow-sm ml-1">
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
            <div class="bg-white border border-slate-200 p-4 rounded-lg">
                <div class="text-xs text-slate-500 font-medium">Total Post Terambil</div>
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

        <!-- Tabs Navigation -->
        <div class="border-b border-slate-200 mb-6 flex gap-6">
            <button id="tab-review-btn" onclick="switchTab('review')" class="py-3 px-1 border-b-2 border-emerald-700 font-semibold text-sm text-emerald-800 flex items-center gap-2">
                Antrean Review
                <span id="badge-review-count" class="bg-emerald-100 text-emerald-800 text-xs px-2 py-0.5 rounded-full font-bold">0</span>
            </button>
            <button id="tab-approved-btn" onclick="switchTab('approved')" class="py-3 px-1 border-b-2 border-transparent font-medium text-sm text-slate-600 hover:text-slate-900 flex items-center gap-2">
                Siap Diposting
                <span id="badge-approved-count" class="bg-slate-100 text-slate-700 text-xs px-2 py-0.5 rounded-full font-bold">0</span>
            </button>
            <button id="tab-settings-btn" onclick="switchTab('settings')" class="py-3 px-1 border-b-2 border-transparent font-medium text-sm text-slate-600 hover:text-slate-900 flex items-center gap-1.5">
                <span>⚙️</span> Pengaturan & Kredensial
            </button>
        </div>

        <!-- Section 1: Antrean Review -->
        <div id="view-review" class="space-y-4">
            <div id="review-loading" class="text-center py-12 text-slate-500">Memuat data antrean...</div>
            <div id="review-empty" class="hidden text-center py-12 bg-white border border-dashed border-slate-300 rounded-lg">
                <p class="text-slate-700 font-medium">Antrean review sedang kosong.</p>
                <p class="text-sm text-slate-500 mt-1">Semua kandidat telah direview, atau klik "Jalankan Pipeline" untuk memeriksa thread baru.</p>
            </div>
            <div id="review-list" class="space-y-4"></div>
        </div>

        <!-- Section 2: Siap Diposting -->
        <div id="view-approved" class="hidden space-y-4">
            <div id="approved-empty" class="hidden text-center py-12 bg-white border border-dashed border-slate-300 rounded-lg">
                <p class="text-slate-700 font-medium">Belum ada draft yang disetujui.</p>
                <p class="text-sm text-slate-500 mt-1">Selesaikan review di tab Antrean Review terlebih dahulu.</p>
            </div>
            <div id="approved-list" class="space-y-4"></div>
        </div>

        <!-- Section 3: Pengaturan & Kredensial -->
        <div id="view-settings" class="hidden space-y-6">
            <div class="bg-white border border-slate-200 rounded-lg p-6 shadow-sm">
                <div class="flex flex-wrap items-center justify-between pb-4 border-b border-slate-200 mb-6 gap-2">
                    <div>
                        <h2 class="text-base font-bold text-slate-900">Pengaturan Kredensial & Validasi API</h2>
                        <p class="text-xs text-slate-500 mt-0.5">Ubah dan uji langsung kredensial Threads API serta AI Anda dengan umpan balik status instan.</p>
                    </div>
                    <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-semibold bg-emerald-50 text-emerald-800 border border-emerald-200">
                        Aktif Otomatis
                    </span>
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
                                <input type="text" id="cfg-threads-user-id" class="w-full text-sm p-2.5 border border-slate-300 rounded-md bg-white focus:ring-2 focus:ring-emerald-600 focus:outline-none" placeholder="me">
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
                                    <span class="text-base">🤖</span> Kredensial AI Model
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
                                <p id="hint-ai-key" class="text-xs text-slate-500 mt-1">Kunci API dari Anthropic, OpenAI, atau OpenRouter.</p>
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
                                    <input type="text" id="cfg-ai-model-custom" class="hidden w-full text-sm p-2.5 border border-slate-300 rounded-md focus:ring-2 focus:ring-emerald-600 focus:outline-none" placeholder="Contoh: anthropic/claude-3.5-sonnet">
                                </div>
                                <p class="text-xs text-slate-500 mt-1">Pilih model untuk validasi relevansi dan drafting balasan.</p>
                            </div>

                            <div class="md:col-span-2">
                                <label class="block text-xs font-semibold text-slate-700 mb-1">Base URL AI (Opsional)</label>
                                <input type="text" id="cfg-ai-base-url" class="w-full text-sm font-mono p-2.5 border border-slate-300 rounded-md focus:ring-2 focus:ring-emerald-600 focus:outline-none" placeholder="https://openrouter.ai/api/v1 (kosongkan untuk URL resmi Anthropic/OpenAI)">
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

        function showToast(msg) {
            const toast = document.getElementById('toast');
            toast.innerText = msg;
            toast.classList.remove('hidden');
            setTimeout(() => toast.classList.add('hidden'), 3500);
        }

        function switchTab(tab) {
            currentTab = tab;
            document.getElementById('view-review').classList.toggle('hidden', tab !== 'review');
            document.getElementById('view-approved').classList.toggle('hidden', tab !== 'approved');
            document.getElementById('view-settings').classList.toggle('hidden', tab !== 'settings');

            const btnRev = document.getElementById('tab-review-btn');
            const btnApp = document.getElementById('tab-approved-btn');
            const btnSet = document.getElementById('tab-settings-btn');

            const activeClass = 'py-3 px-1 border-b-2 border-emerald-700 font-semibold text-sm text-emerald-800 flex items-center gap-2';
            const inactiveClass = 'py-3 px-1 border-b-2 border-transparent font-medium text-sm text-slate-600 hover:text-slate-900 flex items-center gap-2';

            btnRev.className = (tab === 'review') ? activeClass : inactiveClass;
            btnApp.className = (tab === 'approved') ? activeClass : inactiveClass;
            btnSet.className = (tab === 'settings') ? activeClass.replace('gap-2', 'gap-1.5') : inactiveClass.replace('gap-2', 'gap-1.5');

            if (tab === 'review') loadQueue();
            if (tab === 'approved') loadApproved();
            if (tab === 'settings') loadSettings();
        }

        async function fetchStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                document.getElementById('stat-total').innerText = data.total_raw;
                document.getElementById('stat-candidates').innerText = data.total_candidates;
                document.getElementById('stat-pending').innerText = data.pending_review;
                document.getElementById('stat-approved').innerText = data.approved;
                document.getElementById('badge-review-count').innerText = data.pending_review;
                document.getElementById('badge-approved-count').innerText = data.approved;

                // Update Status Pills di Navbar
                const pThreads = document.getElementById('status-pill-threads');
                if (data.threads_configured) {
                    pThreads.className = 'text-xs px-2.5 py-1 rounded border font-medium bg-emerald-50 text-emerald-800 border-emerald-200';
                    pThreads.innerText = 'Threads: Aktif';
                } else {
                    pThreads.className = 'text-xs px-2.5 py-1 rounded border font-medium bg-amber-50 text-amber-800 border-amber-200';
                    pThreads.innerText = 'Threads: Belum Diisi';
                }

                const pAi = document.getElementById('status-pill-ai');
                if (data.ai_configured) {
                    pAi.className = 'text-xs px-2.5 py-1 rounded border font-medium bg-emerald-50 text-emerald-800 border-emerald-200';
                    pAi.innerText = 'AI: ' + (data.ai_model || 'Aktif');
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
            const trimmed = val.trim();
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
            const trimmed = val.trim();

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
                const res = await fetch('/api/validate-key', {
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
                const res = await fetch('/api/validate-key', {
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

        async function loadSettings() {
            try {
                const res = await fetch('/api/settings');
                const data = await res.json();
                const tokenInput = document.getElementById('cfg-threads-token');
                const keyInput = document.getElementById('cfg-ai-key');

                tokenInput.value = data.threads_token || '';
                document.getElementById('cfg-threads-user-id').value = data.threads_user_id || 'me';
                keyInput.value = data.ai_api_key || '';
                document.getElementById('cfg-ai-base-url').value = data.ai_base_url || '';
                document.getElementById('cfg-score-threshold').value = data.score_threshold || 3;

                const selectEl = document.getElementById('cfg-ai-model-select');
                const customEl = document.getElementById('cfg-ai-model-custom');
                const currentModel = data.ai_model || 'claude-3-5-sonnet-20241022';

                let found = false;
                for (let i = 0; i < selectEl.options.length; i++) {
                    if (selectEl.options[i].value === currentModel) {
                        selectEl.selectedIndex = i;
                        found = true;
                        break;
                    }
                }
                if (!found) {
                    selectEl.value = 'custom';
                    customEl.value = currentModel;
                    customEl.classList.remove('hidden');
                } else {
                    customEl.classList.add('hidden');
                }

                // Inisialisasi tampilan hint format
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

            const payload = {
                threads_token: token,
                threads_user_id: document.getElementById('cfg-threads-user-id').value.trim() || 'me',
                ai_api_key: apiKey,
                ai_model: chosenModel || 'claude-3-5-sonnet-20241022',
                ai_base_url: document.getElementById('cfg-ai-base-url').value.trim(),
                score_threshold: parseInt(document.getElementById('cfg-score-threshold').value, 10) || 3
            };

            try {
                const res = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                showToast(data.message || 'Pengaturan berhasil disimpan!');
                fetchStats();

                // Picu uji koneksi otomatis jika ada kunci yang diisi
                if (token) testThreadsConnection();
                if (apiKey) testAiConnection();
            } catch (e) {
                showToast('Gagal menyimpan pengaturan');
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
                const res = await fetch('/api/queue');
                const items = await res.json();
                document.getElementById('review-loading').classList.add('hidden');

                if (items.length === 0) {
                    document.getElementById('review-empty').classList.remove('hidden');
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

        async function loadApproved() {
            document.getElementById('approved-empty').classList.add('hidden');
            document.getElementById('approved-list').innerHTML = '';

            try {
                const res = await fetch('/api/approved');
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

        async function handleReview(postId, action) {
            const textarea = document.getElementById('reply-' + postId);
            const finalReply = textarea ? textarea.value : '';

            try {
                const res = await fetch('/api/review', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ post_id: postId, action: action, final_reply: finalReply })
                });
                const data = await res.json();
                showToast(action === 'approve' ? 'Draft disetujui!' : (action === 'skip' ? 'Post dilewati.' : 'Ditandai selesai!'));
                fetchStats();
                if (currentTab === 'review') loadQueue();
                if (currentTab === 'approved') loadApproved();
            } catch (e) {
                showToast('Gagal memperbarui status');
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
                const res = await fetch('/api/pipeline', { method: 'POST' });
                const data = await res.json();
                showToast(`Selesai: ${data.new_posts} fetch, ${data.classified} filter, ${data.drafted} draft.`);
                fetchStats();
                if (currentTab === 'review') loadQueue();
                if (currentTab === 'approved') loadApproved();
            } catch (e) {
                showToast('Terjadi kesalahan saat menjalankan pipeline');
            } finally {
                btn.disabled = false;
                icon.innerText = '🔄';
                text.innerText = 'Jalankan Pipeline';
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

        // Inisialisasi awal
        fetchStats();
        loadQueue();
        loadSettings();
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index_page():
    return HTML_TEMPLATE

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

from config import THREADS_ACCESS_TOKEN, AI_API_KEY, AI_MODEL, SCORE_THRESHOLD
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

# Inisialisasi basis data
init_db()

app = FastAPI(title="Majelisku Social Assist")

class ReviewRequest(BaseModel):
    post_id: str
    action: str  # "approve" | "skip" | "posted"
    final_reply: Optional[str] = ""

@app.get("/api/stats")
def api_stats():
    return get_stats()

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
    return {
        "status": "ok",
        "new_posts": new_posts,
        "classified": classified,
        "drafted": drafted,
        "stats": get_stats()
    }

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
            <div class="flex items-center gap-2">
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
            <button id="tab-log-btn" onclick="switchTab('log')" class="py-3 px-1 border-b-2 border-transparent font-medium text-sm text-slate-600 hover:text-slate-900">
                Riwayat & Pengaturan
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

        <!-- Section 3: Riwayat & Pengaturan -->
        <div id="view-log" class="hidden space-y-6">
            <div class="bg-white border border-slate-200 rounded-lg p-5">
                <h3 class="font-bold text-slate-900 mb-2">Prinsip & Keamanan Etika</h3>
                <ul class="text-sm text-slate-600 space-y-2 list-disc list-inside">
                    <li><strong>Human-in-the-loop:</strong> Tidak ada bot auto-posting. Semua balasan wajib disetujui dan disalin manual oleh manusia.</li>
                    <li><strong>Relevansi Ketat:</strong> Bridging ke Majelisku.web.id hanya disertakan jika penanya secara eksplisit mencari info kajian.</li>
                    <li><strong>Transparansi:</strong> Jawaban substantif dan tidak manipulatif secara emosi.</li>
                </ul>
            </div>
            <div class="bg-white border border-slate-200 rounded-lg p-5">
                <h3 class="font-bold text-slate-900 mb-3">Informasi Sistem</h3>
                <div class="text-sm text-slate-700 space-y-2">
                    <p><strong>Ambang Batas Skor:</strong> """ + str(SCORE_THRESHOLD) + """ poin</p>
                    <p><strong>AI Model Terkonfigurasi:</strong> """ + AI_MODEL + """</p>
                    <p><strong>Status Kredensial Threads:</strong> """ + ("Terkonfigurasi" if THREADS_ACCESS_TOKEN else "Belum diisi (Mode sampel aman aktif)") + """</p>
                </div>
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
            setTimeout(() => toast.classList.add('hidden'), 3000);
        }

        function switchTab(tab) {
            currentTab = tab;
            document.getElementById('view-review').classList.toggle('hidden', tab !== 'review');
            document.getElementById('view-approved').classList.toggle('hidden', tab !== 'approved');
            document.getElementById('view-log').classList.toggle('hidden', tab !== 'log');

            const btnRev = document.getElementById('tab-review-btn');
            const btnApp = document.getElementById('tab-approved-btn');
            const btnLog = document.getElementById('tab-log-btn');

            const activeClass = 'py-3 px-1 border-b-2 border-emerald-700 font-semibold text-sm text-emerald-800 flex items-center gap-2';
            const inactiveClass = 'py-3 px-1 border-b-2 border-transparent font-medium text-sm text-slate-600 hover:text-slate-900 flex items-center gap-2';

            btnRev.className = (tab === 'review') ? activeClass : inactiveClass;
            btnApp.className = (tab === 'approved') ? activeClass : inactiveClass;
            btnLog.className = (tab === 'log') ? activeClass.replace('flex items-center gap-2', '') : inactiveClass.replace('flex items-center gap-2', '');

            if (tab === 'review') loadQueue();
            if (tab === 'approved') loadApproved();
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
            } catch (e) {
                console.error(e);
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
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index_page():
    return HTML_TEMPLATE

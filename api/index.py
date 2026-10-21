import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Pastikan root directory terbaca dalam Python path
CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db import (
    init_db,
    save_raw_posts,
    get_review_queue,
    get_approved_queue,
    get_skipped_queue,
    get_candidates_without_draft,
    update_draft_review,
    get_stats,
    get_setting,
    set_setting,
)
from classifier import classify_pending_posts
from generate_draft import process_candidates

# ROMBAK 2026-10-22 (perintah user): app ini murni dashboard review.
# - TIDAK ADA integrasi API Threads & tidak ada kredensial apa pun:
#   postingan masuk dari sapuan agen via /api/import-posts (pola yang sama
#   dengan majelisku.web.id), bukan ditarik app dari API resmi.
# - TIDAK ADA layanan AI eksternal: seleksi = classifier kata kunci,
#   draft = mesin lokal berbasis sinyal teks (generate_draft.py).
# - Tombol/endpoint tinggal yang benar-benar dipakai alur review.

init_db()


def _load_seed_leads() -> None:
    # Seed = leads ASLI hasil scrape (data/leads_seed.json), dimuat SEKALI.
    # Flag settings 'seed_v1_done' memastikan seed tidak bangkit lagi.
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


class ReviewRequest(BaseModel):
    post_id: str
    action: str  # "approve" | "skip" | "posted"
    final_reply: Optional[str] = ""


@app.get("/api/stats")
def api_stats():
    return get_stats()


@app.get("/api/queue")
def api_queue():
    queue = get_review_queue()
    if not queue:
        # Antrean kosong tapi ada kandidat belum didraf -> proses otomatis
        if get_candidates_without_draft():
            process_candidates()
            queue = get_review_queue()
    return queue


@app.get("/api/approved")
def api_approved():
    return get_approved_queue()


@app.get("/api/skipped")
def api_skipped():
    return get_skipped_queue()


@app.post("/api/review")
def api_review(req: ReviewRequest):
    if req.action == "approve":
        update_draft_review(
            post_id=req.post_id,
            review_status="approved",
            final_reply=req.final_reply.strip() if req.final_reply else ""
        )
    elif req.action == "skip":
        update_draft_review(post_id=req.post_id, review_status="skipped", final_reply="")
    elif req.action == "posted":
        # final_reply kosong dari UI = pertahankan teks persetujuan yang
        # sudah tersimpan (update_draft_review menimpa kolom apa adanya).
        final = req.final_reply
        if not final:
            for row in get_approved_queue():
                if row.get("post_id") == req.post_id:
                    final = row.get("final_reply") or row.get("draft_reply") or ""
                    break
        update_draft_review(
            post_id=req.post_id,
            review_status="posted",
            final_reply=final
        )
    else:
        raise HTTPException(status_code=400, detail="Aksi tidak dikenal")
    return {"status": "ok", "stats": get_stats()}


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
    """Import postingan hasil sapuan agen (atau tempel manual dari UI).

    Setiap postingan langsung melewati saringan classifier + penyusunan
    draft secara otomatis; yang lolos muncul di Antrean Review.
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
    """Proses antrean: saring postingan pending + susun draft kandidat.

    Tidak menarik data dari mana pun — data masuk hanya lewat sapuan
    agen / import manual.
    """
    classified = classify_pending_posts()
    drafted = process_candidates()
    return {
        "status": "ok",
        "classified": classified,
        "drafted": drafted,
        "stats": get_stats(),
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
  .toast { transition: opacity .3s ease; }
</style>
</head>
<body class="bg-emerald-950 text-emerald-50 min-h-screen">
<div class="max-w-5xl mx-auto px-4 py-6">

  <header class="flex flex-wrap items-center justify-between gap-3 mb-5">
    <div>
      <h1 class="text-2xl font-bold">🕌 Majelisku Social Assist</h1>
      <p class="text-emerald-300 text-sm">Antrean review balasan untuk pencari kajian di Threads — sumber dari sapuan otomatis, tanpa auto-posting.</p>
    </div>
    <button onclick="loadAll()" class="bg-emerald-800 hover:bg-emerald-700 px-3 py-2 rounded-lg text-sm">↻ Muat Ulang</button>
  </header>

  <section class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6" id="stats"></section>

  <section class="bg-emerald-900/60 border border-emerald-800 rounded-2xl p-4 mb-6">
    <div class="flex flex-wrap items-center gap-3">
      <h2 class="font-semibold text-lg mr-auto">📥 Postingan Masuk</h2>
      <button onclick="runPipeline()" class="bg-emerald-600 hover:bg-emerald-500 px-4 py-2 rounded-lg text-sm font-medium">🔄 Proses Antrean</button>
      <button onclick="toggleImport()" class="bg-emerald-800 hover:bg-emerald-700 px-4 py-2 rounded-lg text-sm">📥 Import Manual</button>
    </div>
    <div id="importBox" class="hidden mt-4">
      <p class="text-sm text-emerald-300 mb-2">Satu postingan per baris, format JSON: {"author":"nama","text":"isi postingan","url":"https://www.threads.com/...","created_at":"2026-10-22T00:00:00Z"}</p>
      <textarea id="importText" rows="5" class="w-full bg-emerald-950 border border-emerald-800 rounded-lg p-3 text-sm" placeholder='{"author":"contoh","text":"cari kajian sunnah di bandung dimana ya?","url":"https://www.threads.com/@contoh/post/xxxx"}'></textarea>
      <button onclick="doImport()" class="mt-2 bg-emerald-600 hover:bg-emerald-500 px-4 py-2 rounded-lg text-sm font-medium">Import &amp; Proseskan</button>
    </div>
    <p id="intakeInfo" class="text-sm text-emerald-300 mt-3"></p>
  </section>

  <section class="mb-8">
    <h2 class="font-semibold text-lg mb-3">👀 Antrean Review <span id="queueCount" class="text-emerald-300 text-sm"></span></h2>
    <div id="queue" class="space-y-4"></div>
  </section>

  <section class="mb-8">
    <h2 class="font-semibold text-lg mb-3">✅ Disetujui <span id="approvedCount" class="text-emerald-300 text-sm"></span></h2>
    <div id="approved" class="space-y-4"></div>
  </section>

  <section class="mb-8">
    <h2 class="font-semibold text-lg mb-3">⏭️ Dilewati <span id="skippedCount" class="text-emerald-300 text-sm"></span></h2>
    <div id="skipped" class="space-y-3"></div>
  </section>

</div>
<div id="toast" class="toast fixed bottom-5 left-1/2 -translate-x-1/2 bg-emerald-600 text-white px-4 py-2 rounded-lg shadow-lg opacity-0 pointer-events-none text-sm"></div>

<script>
const $ = id => document.getElementById(id);
function esc(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function toast(msg){ const t=$('toast'); t.textContent=msg; t.style.opacity=1; setTimeout(()=>t.style.opacity=0, 2600); }
async function api(path, opts){ const r = await fetch(path, opts); if(!r.ok){ throw new Error('HTTP '+r.status); } return r.json(); }
function fmtTime(iso){ if(!iso) return ''; const d=new Date(iso); return isNaN(d) ? esc(iso) : d.toLocaleString('id-ID'); }
function sourceBtn(url){ return url ? `<a href="${esc(url)}" target="_blank" rel="noopener" class="bg-sky-700 hover:bg-sky-600 px-3 py-1.5 rounded-lg text-sm font-medium">🔗 Lihat di Threads</a>` : ''; }
function profileLink(author){ return author ? `<a href="https://www.threads.com/@${esc(author)}" target="_blank" rel="noopener" class="font-semibold text-emerald-200 hover:underline">@${esc(author)}</a>` : '<span class="font-semibold">(tanpa nama)</span>'; }
async function copyText(txt){ try { await navigator.clipboard.writeText(txt); } catch(e){ const ta=document.createElement('textarea'); ta.value=txt; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); } toast('📋 Tersalin'); }

async function loadAll(){
  try {
    const [stats, queue, approved, skipped] = await Promise.all([api('/api/stats'), api('/api/queue'), api('/api/approved'), api('/api/skipped')]);
    renderStats(stats); renderQueue(queue); renderApproved(approved); renderSkipped(skipped);
  } catch(e){ toast('Gagal memuat data: '+e.message); }
}

function renderStats(s){
  const items = [
    ['Total Masuk', s.total_raw ?? 0], ['Kandidat', s.total_candidates ?? 0],
    ['Menunggu Review', s.pending_review ?? 0], ['Disetujui', s.approved ?? 0], ['Dilewati', s.skipped ?? 0]
  ];
  $('stats').innerHTML = items.map(([k,v]) => `<div class="bg-emerald-900/60 border border-emerald-800 rounded-xl p-3 text-center"><div class="text-2xl font-bold">${v}</div><div class="text-xs text-emerald-300">${k}</div></div>`).join('');
  $('intakeInfo').textContent = `Total ${s.total_raw ?? 0} postingan pernah masuk. Sapuan otomatis berjalan di sisi agen tiap 15 menit — postingan baru muncul di sini setelah disaring.`;
}

function renderQueue(queue){
  $('queueCount').textContent = queue.length ? `(${queue.length})` : '';
  $('queue').innerHTML = queue.length ? queue.map(it => `
  <article class="bg-emerald-900/60 border border-emerald-800 rounded-2xl p-4">
    <div class="flex flex-wrap items-center gap-2 mb-2">
      ${profileLink(it.author)}
      <span class="text-xs text-emerald-400">${fmtTime(it.created_at)}</span>
      <span class="ml-auto text-xs bg-emerald-800 px-2 py-0.5 rounded-full">Skor ${it.score ?? '-'}</span>
    </div>
    <p class="whitespace-pre-wrap text-[15px] mb-3">${esc(it.text)}</p>
    <label class="text-xs text-emerald-300">Draft balasan (bisa diedit):</label>
    <textarea id="draft-${esc(it.post_id)}" rows="3" class="w-full mt-1 bg-emerald-950 border border-emerald-700 rounded-lg p-3 text-sm">${esc(it.draft_reply || '')}</textarea>
    ${it.ai_reasoning ? `<p class="text-xs text-emerald-400 italic mt-2">🧭 ${esc(it.ai_reasoning)}</p>` : ''}
    <div class="flex flex-wrap gap-2 mt-3">
      ${sourceBtn(it.url)}
      <button onclick="copyText(document.getElementById('draft-${esc(it.post_id)}').value)" class="bg-emerald-800 hover:bg-emerald-700 px-3 py-1.5 rounded-lg text-sm">📋 Salin Draft</button>
      <button onclick="review('${esc(it.post_id)}','approve')" class="bg-emerald-500 hover:bg-emerald-400 text-emerald-950 px-3 py-1.5 rounded-lg text-sm font-semibold">✅ Setujui</button>
      <button onclick="review('${esc(it.post_id)}','skip')" class="bg-red-900/70 hover:bg-red-800 px-3 py-1.5 rounded-lg text-sm">⏭️ Lewati</button>
    </div>
  </article>`).join('') : '<p class="text-emerald-400 text-sm">Antrean kosong — belum ada kandidat baru dari sapuan.</p>';
}

function renderApproved(list){
  $('approvedCount').textContent = list.length ? `(${list.length})` : '';
  $('approved').innerHTML = list.length ? list.map(it => `
  <article class="bg-emerald-900/40 border border-emerald-800 rounded-2xl p-4">
    <div class="flex flex-wrap items-center gap-2 mb-2">
      ${profileLink(it.author)}
      <span class="text-xs px-2 py-0.5 rounded-full ${it.review_status==='posted' ? 'bg-sky-800' : 'bg-emerald-800'}">${it.review_status==='posted' ? '✔️ Sudah dibalas' : 'Disetujui'}</span>
      <span class="text-xs text-emerald-400">${fmtTime(it.created_at)}</span>
    </div>
    <p class="whitespace-pre-wrap text-sm text-emerald-300 mb-2">${esc(it.text)}</p>
    <div class="bg-emerald-950 border border-emerald-800 rounded-lg p-3 text-sm whitespace-pre-wrap">${esc(it.final_reply || it.draft_reply || '')}</div>
    <div class="flex flex-wrap gap-2 mt-3">
      ${sourceBtn(it.url)}
      <button onclick="copyText(${JSON.stringify(it.final_reply || it.draft_reply || '').replace(/"/g,'&quot;')})" class="bg-emerald-800 hover:bg-emerald-700 px-3 py-1.5 rounded-lg text-sm">📋 Salin Balasan</button>
      ${it.review_status!=='posted' ? `<button onclick="review('${esc(it.post_id)}','posted')" class="bg-sky-800 hover:bg-sky-700 px-3 py-1.5 rounded-lg text-sm">✔️ Tandai Sudah Dibalas</button>` : ''}
    </div>
  </article>`).join('') : '<p class="text-emerald-400 text-sm">Belum ada yang disetujui.</p>';
}

function renderSkipped(list){
  $('skippedCount').textContent = list.length ? `(${list.length})` : '';
  $('skipped').innerHTML = list.length ? list.map(it => `
  <article class="bg-emerald-900/30 border border-emerald-900 rounded-xl p-3 flex flex-wrap items-center gap-2">
    ${profileLink(it.author)}
    <span class="text-sm text-emerald-300 flex-1 min-w-[200px]">${esc((it.text || '').slice(0, 140))}</span>
    ${sourceBtn(it.url)}
  </article>`).join('') : '<p class="text-emerald-400 text-sm">Belum ada yang dilewati.</p>';
}

async function review(postId, action){
  const body = { post_id: postId, action };
  if(action === 'approve'){ const ta = $('draft-'+postId); body.final_reply = ta ? ta.value : ''; }
  if(action === 'posted'){ body.final_reply = ''; }
  try { await api('/api/review', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) }); toast(action==='skip' ? '⏭️ Dilewati' : '✅ Tersimpan'); loadAll(); }
  catch(e){ toast('Gagal: '+e.message); }
}

async function runPipeline(){
  try { const r = await api('/api/pipeline', { method:'POST' }); toast(`🔄 Selesai — tersaring ${r.classified}, draft baru ${r.drafted}`); loadAll(); }
  catch(e){ toast('Gagal: '+e.message); }
}

function toggleImport(){ $('importBox').classList.toggle('hidden'); }

async function doImport(){
  const lines = $('importText').value.split('\\n').map(s => s.trim()).filter(Boolean);
  const posts = [];
  for(const line of lines){ try { posts.push(JSON.parse(line)); } catch(e){ toast('Ada baris JSON tidak valid'); return; } }
  if(!posts.length){ toast('Tidak ada postingan untuk diimport'); return; }
  try { const r = await api('/api/import-posts', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ posts }) }); toast(`📥 Import: ${r.inserted} baru, ${r.drafted} draft dibuat`); $('importText').value=''; loadAll(); }
  catch(e){ toast('Gagal import: '+e.message); }
}

loadAll();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_TEMPLATE

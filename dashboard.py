import streamlit as st
from datetime import datetime
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

# Inisialisasi konfigurasi halaman Streamlit
st.set_page_config(
    page_title="Majelisku Social Assist",
    page_icon="🕌",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Inisialisasi skema tabel jika belum ada
init_db()

# Styling khusus antislop (kontras tinggi, tipografi bersih, tanpa gradien generik)
st.markdown("""
<style>
    /* Reset & Tipografi */
    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }
    
    /* Tombol Utama */
    .stButton button {
        border-radius: 6px;
        font-weight: 500;
        padding: 0.5rem 1rem;
        transition: all 0.15s ease-in-out;
    }
    
    /* Card Kontainer Thread */
    .thread-card {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1.25rem;
        margin-bottom: 1.25rem;
    }
    
    /* Status Badge */
    .badge-relevant {
        background-color: #ecfdf5;
        color: #065f46;
        border: 1px solid #a7f3d0;
        padding: 0.2rem 0.55rem;
        border-radius: 4px;
        font-size: 0.825rem;
        font-weight: 600;
    }
    
    .badge-irrelevant {
        background-color: #fef2f2;
        color: #991b1b;
        border: 1px solid #fecaca;
        padding: 0.2rem 0.55rem;
        border-radius: 4px;
        font-size: 0.825rem;
        font-weight: 600;
    }

    .badge-score {
        background-color: #f1f5f9;
        color: #334155;
        border: 1px solid #cbd5e1;
        padding: 0.2rem 0.55rem;
        border-radius: 4px;
        font-size: 0.825rem;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# Sidebar: Kontrol Pipeline dan Status Kredensial
st.sidebar.title("Majelisku Assist")
st.sidebar.caption("Asisten review postingan Threads untuk Majelisku.web.id")

st.sidebar.divider()
st.sidebar.subheader("Status Konfigurasi")

# Indikator Kredensial Threads
if THREADS_ACCESS_TOKEN:
    st.sidebar.success("Threads Token: Terkonfigurasi")
else:
    st.sidebar.warning("Threads Token: Belum diisi (.env)")

# Indikator Kredensial AI
if AI_API_KEY:
    st.sidebar.success(f"AI Model: {AI_MODEL}")
else:
    st.sidebar.info("AI API Key: Mode Fallback Aktif")

st.sidebar.divider()
st.sidebar.subheader("Sinkronisasi Data")

if st.sidebar.button("Jalankan Pipeline Lengkap", use_container_width=True, type="primary"):
    with st.spinner("Menjalankan pipeline (Fetch, Classifier, Draft Generator)..."):
        # 1. Fetch
        new_posts = run_fetch()
        # 2. Classifier
        classified = classify_pending_posts()
        # 3. Generate Draft
        drafted = process_candidates()
        st.sidebar.success(f"Selesai: {new_posts} fetch, {classified} filter, {drafted} draft.")
        st.rerun()

# Statistik Riil dari Database
stats = get_stats()
st.sidebar.divider()
st.sidebar.subheader("Statistik Database")
col_s1, col_s2 = st.sidebar.columns(2)
col_s1.metric("Total Post", stats["total_raw"])
col_s2.metric("Kandidat", stats["total_candidates"])
col_s3, col_s4 = st.sidebar.columns(2)
col_s3.metric("Perlu Review", stats["pending_review"])
col_s4.metric("Disetujui", stats["approved"])

# Halaman Utama: Header
st.title("Review Balasan Threads")
st.write("Verifikasi kesesuaian thread pencarian kajian sebelum balasan disalin ke Threads.")

# Tab Navigasi Antarmuka
tab_review, tab_ready, tab_history = st.tabs([
    f"Antrean Review ({stats['pending_review']})",
    f"Siap Diposting ({stats['approved']})",
    "Riwayat & Log"
])

# TAB 1: Antrean Review
with tab_review:
    queue = get_review_queue()

    if not queue:
        st.info("Antrean review sedang kosong. Klik tombol 'Jalankan Pipeline Lengkap' di sidebar untuk memeriksa postingan baru.")
    else:
        st.write(f"Menampilkan {len(queue)} kandidat yang membutuhkan validasi admin.")

        for item in queue:
            post_id = item["post_id"]
            with st.container():
                st.markdown(f"""
                <div class="thread-card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem;">
                        <div>
                            <strong>@{item['author']}</strong>
                            <span style="color: #64748b; font-size: 0.85rem; margin-left: 0.5rem;">{item['created_at'][:19].replace('T', ' ')}</span>
                        </div>
                        <div>
                            <span class="badge-score">Skor Filter: {item['score']}</span>
                            <span class="{'badge-relevant' if item['is_relevant'] else 'badge-irrelevant'}" style="margin-left: 0.4rem;">
                                {'Relevan' if item['is_relevant'] else 'Kurang Relevan'}
                            </span>
                        </div>
                    </div>
                    <div style="font-size: 1.05rem; line-height: 1.5; color: #1e293b; margin-bottom: 0.75rem;">
                        "{item['text']}"
                    </div>
                    <div style="font-size: 0.875rem; color: #475569; margin-bottom: 0.5rem;">
                        <strong>Pertimbangan AI:</strong> {item['ai_reasoning']}
                    </div>
                    <div>
                        <a href="{item['url']}" target="_blank" style="color: #0f766e; text-decoration: none; font-size: 0.875rem; font-weight: 500;">
                            Buka tautan thread asli &rarr;
                        </a>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                # Form Edit & Approval per Item
                form_key = f"review_form_{post_id}"
                with st.form(key=form_key):
                    st.write("**Draft Balasan:**")
                    edited_reply = st.text_area(
                        label=f"Draft untuk {item['author']}",
                        value=item["draft_reply"] or "",
                        height=95,
                        label_visibility="collapsed",
                        key=f"text_{post_id}"
                    )

                    col_btn1, col_btn2, col_btn3 = st.columns([2, 2, 6])

                    with col_btn1:
                        btn_approve = st.form_submit_button("Setujui Draft", type="primary", use_container_width=True)
                    with col_btn2:
                        btn_skip = st.form_submit_button("Lewati (Skip)", use_container_width=True)

                    if btn_approve:
                        update_draft_review(
                            post_id=post_id,
                            review_status="approved",
                            final_reply=edited_reply.strip()
                        )
                        st.success(f"Post @{item['author']} telah disetujui.")
                        st.rerun()

                    if btn_skip:
                        update_draft_review(
                            post_id=post_id,
                            review_status="skipped",
                            final_reply=""
                        )
                        st.warning(f"Post @{item['author']} telah dilewati.")
                        st.rerun()

                st.write("")

# TAB 2: Siap Diposting (Approved)
with tab_ready:
    approved_list = get_approved_queue()

    if not approved_list:
        st.info("Belum ada draft yang disetujui. Selesaikan review di tab 'Antrean Review' terlebih dahulu.")
    else:
        st.write(f"Terdapat {len(approved_list)} balasan yang siap disalin dan diposting secara manual ke Threads.")

        for item in approved_list:
            post_id = item["post_id"]
            with st.container():
                st.markdown(f"""
                <div class="thread-card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
                        <strong>Target: @{item['author']}</strong>
                        <span style="font-size: 0.85rem; color: #64748b;">Disetujui: {item['reviewed_at'][:19].replace('T', ' ')}</span>
                    </div>
                    <div style="font-size: 0.925rem; color: #475569; margin-bottom: 0.75rem; background-color: #f8fafc; padding: 0.6rem; border-radius: 4px;">
                        <em>"{item['text']}"</em>
                    </div>
                    <div style="margin-bottom: 0.5rem;">
                        <strong>Teks Balasan Siap Salin:</strong>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                # Kotak teks yang mudah disalin
                st.code(item["final_reply"] or item["draft_reply"], language="markdown")

                col_act1, col_act2, col_spacer = st.columns([2, 2, 6])
                with col_act1:
                    st.link_button(
                        label="Buka Threads untuk Membalas",
                        url=item["url"],
                        use_container_width=True
                    )
                with col_act2:
                    if st.button("Tandai Selesai Diposting", key=f"mark_posted_{post_id}", use_container_width=True):
                        update_draft_review(
                            post_id=post_id,
                            review_status="posted",
                            final_reply=item["final_reply"]
                        )
                        st.success("Telah ditandai selesai.")
                        st.rerun()

                st.divider()

# TAB 3: Riwayat & Log
with tab_history:
    st.subheader("Ringkasan Filter dan Aktivitas")
    st.write("Semua proses berjalan secara lokal pada database SQLite:")
    st.code(f"Lokasi Database: config.DB_PATH\nAmbang Batas Skor Relevansi: {SCORE_THRESHOLD}", language="text")

    st.write("**Daftar Status Saat Ini:**")
    st.table({
        "Kategori": [
            "Total Post Terambil (raw_posts)",
            "Lolos Filter Kata Kunci (status: candidate)",
            "Menunggu Review Admin (pending_review)",
            "Disetujui & Siap Posting (approved)",
            "Dilewati / Ditolak (skipped)"
        ],
        "Jumlah": [
            stats["total_raw"],
            stats["total_candidates"],
            stats["pending_review"],
            stats["approved"],
            stats["skipped"]
        ]
    })

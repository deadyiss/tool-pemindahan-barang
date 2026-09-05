import math
import os
import tempfile

import pandas as pd
import streamlit as st

import db
from parser import parse_file

st.set_page_config(page_title="Cek Pemindahan Barang", layout="wide")

TANGGAL_MINIMAL = "2026-08-01"
HALAMAN_SIZE = 50  # jumlah baris per halaman di tabel "Data pemindahan tersimpan"

APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "ujangadmin")


# ---------------------------------------------------------------------------
# Autentikasi -- 1 user shared.
# CATATAN JUJUR: autocomplete="off"/"new-password" cuma "permintaan" ke
# browser, bukan jaminan -- browser modern kadang tetap menawarkan simpan
# password walau atributnya diset.
# ---------------------------------------------------------------------------
def require_login():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if st.session_state.authenticated:
        return

    _, col_tengah, _ = st.columns([1, 1.2, 1])
    with col_tengah:
        with st.container(border=True):
            st.title("Login")
            st.caption("Tool internal -- masukkan username & password untuk akses.")
            with st.form("login_form", clear_on_submit=False):
                user = st.text_input("Username", autocomplete="off")
                pw = st.text_input("Password", type="password", autocomplete="new-password")
                submitted = st.form_submit_button("Masuk")
    if submitted:
        if user == APP_USERNAME and pw == APP_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Username atau password salah.")
    st.stop()


require_login()
db.init_db()

st.title("Tool Cek Validitas Barang — Penjualan Cabang")

tab_cek, tab_crud, tab_backfill, tab_log = st.tabs(
    ["Pengecekan Harian", "Kelola Data Pemindahan", "Backfill (Import Massal)", "Riwayat Import"]
)

# ---------------------------------------------------------------------------
# TAB 1 — Pengecekan harian (file) + cek manual satu barang (tanpa file)
# ---------------------------------------------------------------------------
with tab_cek:
    st.subheader("Upload Excel penjualan cabang hari ini")
    st.caption(
        "Upload 1 file (format Movement Stock atau Daftar Stok, dideteksi otomatis). "
        "Tool menampilkan barang yang terjual; qty diganti 0 kalau barang tidak "
        f"ditemukan pernah dipindahkan ke cabang tsb sejak {TANGGAL_MINIMAL}."
    )
    uploaded = st.file_uploader("File Excel", type=["xlsx"], key="cek_upload")

    if "auto_capture_state" not in st.session_state:
        st.session_state.auto_capture_state = {}  # file_id -> {"ids":[...], "n_baru":n, "n_dup":n}

    if uploaded is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(uploaded.getbuffer())
            tmp_path = tmp.name

        try:
            parsed = parse_file(tmp_path)
        except Exception as e:
            st.error(f"Gagal membaca file: {e}")
        else:
            st.write(
                f"**Cabang:** {parsed.cabang}  |  **Format:** {parsed.format_sumber}  |  "
                f"**Tanggal:** {parsed.tanggal_awal}"
            )

            # --- Auto-capture ke histori (opsional, checkbox) ---
            n_masuk_kandidat = sum(1 for r in parsed.rows if r.qty_masuk > 0)
            simpan_histori = st.checkbox(
                "Simpan juga baris Receive/Masuk file ini ke histori pemindahan",
                value=True,
                key="simpan_histori_harian",
                help=(
                    "Aktif = file ini sekaligus menambah histori pemindahan (dedup otomatis). "
                    "Matikan lagi kapan saja -- record yang SUDAH tersimpan dari file ini akan "
                    "otomatis dihapus lagi (bukan cuma berhenti menambah yang baru)."
                ),
            )
            file_key = uploaded.file_id
            state = st.session_state.auto_capture_state.get(file_key)

            if simpan_histori:
                if state is None and n_masuk_kandidat > 0:
                    with db.get_conn() as conn:
                        id_baru = db.simpan_pemindahan_dari_parsed(
                            conn, parsed, sumber="backfill_excel", file_asal=uploaded.name
                        )
                        n_baru = len(id_baru)
                        n_dup = n_masuk_kandidat - n_baru
                        db.log_import(
                            conn,
                            nama_file=uploaded.name,
                            cabang=parsed.cabang,
                            format_sumber=parsed.format_sumber,
                            tanggal_data=parsed.tanggal_awal,
                            n_baru=n_baru,
                            n_duplikat=n_dup,
                            konteks="pengecekan_harian",
                        )
                    st.session_state.auto_capture_state[file_key] = {
                        "ids": id_baru, "n_baru": n_baru, "n_dup": n_dup,
                    }
                    state = st.session_state.auto_capture_state[file_key]
                if state:
                    st.info(
                        f"Histori pemindahan diperbarui dari file ini: **{state['n_baru']} record baru** "
                        f"tersimpan, **{state['n_dup']} sudah ada sebelumnya** (di-skip, tidak dobel)."
                    )
            else:
                if state is not None:
                    with db.get_conn() as conn:
                        db.delete_pemindahan_batch(conn, state["ids"])
                    st.warning(
                        f"Dibatalkan: {len(state['ids'])} record yang sempat tersimpan dari file "
                        "ini sudah dihapus lagi dari histori."
                    )
                    del st.session_state.auto_capture_state[file_key]
                elif n_masuk_kandidat > 0:
                    st.caption(
                        f"({n_masuk_kandidat} baris Receive/Masuk > 0 di file ini TIDAK disimpan "
                        "ke histori karena checkbox di atas dimatikan.)"
                    )

            # --- Pengecekan validitas barang terjual ---
            terjual_rows = [r for r in parsed.rows if r.qty_terjual > 0]
            if not terjual_rows:
                st.info("Tidak ada barang dengan penjualan > 0 di file ini.")
            else:
                with db.get_conn() as conn:
                    hasil = []
                    for r in terjual_rows:
                        valid = db.cek_validitas(conn, parsed.cabang, r.sku, TANGGAL_MINIMAL)
                        hasil.append(
                            {
                                "SKU": r.sku,
                                "Nama Barang": r.nama_barang,
                                "Qty Terjual (asli)": r.qty_terjual,
                                "Valid?": "Ya" if valid else "Tidak",
                                "Qty Output": r.qty_terjual if valid else 0,
                            }
                        )
                df = pd.DataFrame(hasil)
                n_valid = (df["Valid?"] == "Ya").sum()
                n_invalid = (df["Valid?"] == "Tidak").sum()
                c1, c2, c3 = st.columns(3)
                c1.metric("Total barang terjual", len(df))
                c2.metric("Valid (boleh diinput)", n_valid)
                c3.metric("Tidak valid (qty -> 0)", n_invalid)

                st.dataframe(df, width='stretch', hide_index=True)

                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button("Download hasil (CSV)", csv, "hasil_cek.csv", "text/csv")

        os.unlink(tmp_path)

    st.divider()
    st.subheader("Cek cepat 1 barang (tanpa upload file)")
    st.caption(
        "Pilih cabang & barang langsung, tanpa perlu punya file excel-nya -- "
        "berguna untuk cek satu barang secara spontan."
    )
    with db.get_conn() as conn:
        daftar_cabang_cek = db.list_cabang(conn)

    if not daftar_cabang_cek:
        st.info("Belum ada data cabang. Lakukan backfill dulu di tab 'Backfill'.")
    else:
        cc1, cc2, cc3 = st.columns([1, 2, 1])
        with cc1:
            cabang_manual = st.selectbox("Cabang", daftar_cabang_cek, key="manual_cek_cabang")
        with db.get_conn() as conn:
            fmt_manual = db.get_format_cabang(conn, cabang_manual)
            barang_manual_list = db.list_barang(conn, fmt_manual) if fmt_manual else []
        barang_manual_opsi = {f"{sku} — {nama}": (sku, nama) for sku, nama in barang_manual_list}
        with cc2:
            barang_manual_pilih = st.selectbox(
                "Barang", list(barang_manual_opsi.keys()), key="manual_cek_barang"
            )
        with cc3:
            qty_manual = st.number_input("Qty terjual", min_value=0.0, step=1.0, key="manual_cek_qty")

        if st.button("Cek Validitas", key="manual_cek_button"):
            sku_m, nama_m = barang_manual_opsi[barang_manual_pilih]
            with db.get_conn() as conn:
                valid_m = db.cek_validitas(conn, cabang_manual, sku_m, TANGGAL_MINIMAL)
            if valid_m:
                st.success(
                    f"VALID -- {nama_m} pernah dipindah ke {cabang_manual} sejak "
                    f"{TANGGAL_MINIMAL}. Qty Output: **{qty_manual}**"
                )
            else:
                st.error(
                    f"TIDAK VALID -- {nama_m} tidak ditemukan pernah dipindah ke "
                    f"{cabang_manual} sejak {TANGGAL_MINIMAL}. Qty Output: **0**"
                )

# ---------------------------------------------------------------------------
# TAB 2 — CRUD pemindahan barang (+ klasifikasi per tanggal + pagination)
# ---------------------------------------------------------------------------
with tab_crud:
    st.subheader("Tambah record pemindahan barang")
    st.caption(
        "Input **manual satu-satu** (tanpa upload file) -- untuk mencatat pemindahan "
        "yang belum sempat masuk lewat file excel, atau menambah data lama secara langsung."
    )

    with db.get_conn() as conn:
        daftar_cabang = db.list_cabang(conn)

    if not daftar_cabang:
        st.warning("Belum ada data cabang. Lakukan backfill dulu di tab 'Backfill'.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            cabang_pilih = st.selectbox("Cabang", daftar_cabang, key="add_cabang")
        with db.get_conn() as conn:
            fmt_cabang = db.get_format_cabang(conn, cabang_pilih)
            list_barang = db.list_barang(conn, fmt_cabang) if fmt_cabang else []

        barang_opsi = {f"{sku} — {nama}": (sku, nama) for sku, nama in list_barang}
        with col2:
            barang_pilih = st.selectbox(
                "Barang (dropdown, bukan ketik bebas)", list(barang_opsi.keys()), key="add_barang"
            )

        col3, col4 = st.columns(2)
        with col3:
            tanggal_pilih = st.date_input("Tanggal pemindahan", key="add_tanggal")
        with col4:
            qty_pilih = st.number_input("Qty", min_value=0.0, step=1.0, key="add_qty")

        if st.button("Tambah record"):
            sku, nama = barang_opsi[barang_pilih]
            with db.get_conn() as conn:
                id_baru = db.insert_pemindahan(
                    conn,
                    format_sumber=fmt_cabang,
                    sku=sku,
                    nama_barang=nama,
                    cabang=cabang_pilih,
                    tanggal_pindah=str(tanggal_pilih),
                    qty=qty_pilih,
                    sumber="manual_input",
                )
            if id_baru is not None:
                st.success(f"Record ditambahkan: {nama} -> {cabang_pilih} ({tanggal_pilih})")
            else:
                st.warning(
                    "Record IDENTIK (barang+cabang+tanggal+qty ini) sudah ada sebelumnya -- "
                    "tidak disimpan dobel."
                )
            st.rerun()

    st.divider()
    st.subheader("Klasifikasi: barang masuk tanggal berapa saja")
    st.caption("Pilih cabang untuk melihat semua tanggal yang punya record pemindahan.")

    if daftar_cabang:
        cabang_klas = st.selectbox("Cabang", daftar_cabang, key="klas_cabang")
        with db.get_conn() as conn:
            tanggal_rows = db.list_tanggal_pemindahan(conn, cabang_klas)
        if tanggal_rows:
            df_tgl = pd.DataFrame(
                tanggal_rows, columns=["Tanggal", "Jumlah Barang", "Total Qty"]
            )
            st.dataframe(df_tgl, width='stretch', hide_index=True)

            tanggal_opsi = [r[0] for r in tanggal_rows]
            dc1, dc2 = st.columns([2, 1])
            with dc1:
                tanggal_detail_pilih = st.selectbox(
                    "Pilih tanggal untuk lihat rincian barangnya", tanggal_opsi, key="klas_tanggal_pilih"
                )
            with dc2:
                st.write("")
                lihat_detail = st.button("Lihat Detail", key="klas_lihat_detail")

            if lihat_detail:
                with db.get_conn() as conn:
                    detail_rows = db.list_detail_tanggal(conn, cabang_klas, tanggal_detail_pilih)
                if detail_rows:
                    df_detail = pd.DataFrame(detail_rows, columns=["SKU", "Nama Barang", "Qty"])
                    st.dataframe(df_detail, width='stretch', hide_index=True)
                else:
                    st.info("Tidak ada rincian ditemukan.")
        else:
            st.info(f"Belum ada record pemindahan untuk {cabang_klas}.")

    st.divider()
    st.subheader("Data pemindahan tersimpan (untuk edit/hapus — revisi nota)")

    fc1, fc2 = st.columns([1, 2])
    with fc1:
        filter_cabang = st.selectbox(
            "Filter cabang", ["(semua)"] + daftar_cabang, key="filter_cabang"
        ) if daftar_cabang else "(semua)"
    with fc2:
        search_term = st.text_input(
            "Cari nama barang / SKU", key="search_pemindahan", placeholder="mis. Anggur Merah, atau A01"
        )

    filter_key = f"{filter_cabang}|{search_term}"
    if st.session_state.get("_last_filter_key") != filter_key:
        st.session_state["_last_filter_key"] = filter_key
        st.session_state["crud_page"] = 0
    if "crud_page" not in st.session_state:
        st.session_state["crud_page"] = 0

    cabang_arg = None if filter_cabang == "(semua)" else filter_cabang
    search_arg = search_term.strip() or None

    with db.get_conn() as conn:
        total_rows = db.count_pemindahan(conn, cabang=cabang_arg, search=search_arg)
    total_halaman = max(1, math.ceil(total_rows / HALAMAN_SIZE))
    st.session_state["crud_page"] = min(st.session_state["crud_page"], total_halaman - 1)

    with db.get_conn() as conn:
        rows = db.list_pemindahan(
            conn,
            cabang=cabang_arg,
            search=search_arg,
            limit=HALAMAN_SIZE,
            offset=st.session_state["crud_page"] * HALAMAN_SIZE,
        )

    if rows:
        df_rows = pd.DataFrame(
            rows, columns=["ID", "Cabang", "SKU", "Nama Barang", "Tanggal", "Qty", "Sumber"]
        )
        st.dataframe(df_rows, width='stretch', hide_index=True)

        pc1, pc2, pc3 = st.columns([1, 2, 1])
        with pc1:
            if st.button("⬅ Sebelumnya", disabled=st.session_state["crud_page"] <= 0):
                st.session_state["crud_page"] -= 1
                st.rerun()
        with pc2:
            st.markdown(
                f"<div style='text-align:center'>Halaman {st.session_state['crud_page'] + 1} "
                f"dari {total_halaman} — total {total_rows} record</div>",
                unsafe_allow_html=True,
            )
        with pc3:
            if st.button("Berikutnya ➡", disabled=st.session_state["crud_page"] >= total_halaman - 1):
                st.session_state["crud_page"] += 1
                st.rerun()

        st.markdown("**Edit / Hapus record** (untuk koreksi kalau ada revisi nota)")
        id_target = st.number_input("ID record", min_value=0, step=1, key="edit_id")
        ec1, ec2, ec3 = st.columns(3)
        with ec1:
            new_tanggal = st.date_input("Tanggal baru", key="edit_tanggal")
        with ec2:
            new_qty = st.number_input("Qty baru", min_value=0.0, step=1.0, key="edit_qty")
        with ec3:
            st.write("")
            st.write("")
            colA, colB = st.columns(2)
            if colA.button("Update"):
                with db.get_conn() as conn:
                    db.update_pemindahan(conn, id_target, str(new_tanggal), new_qty)
                st.success(f"Record ID {id_target} diupdate.")
                st.rerun()
            if colB.button("Hapus", type="secondary"):
                with db.get_conn() as conn:
                    db.delete_pemindahan(conn, id_target)
                st.success(f"Record ID {id_target} dihapus.")
                st.rerun()
    else:
        st.info("Tidak ada record yang cocok dengan filter/pencarian ini.")

    st.divider()
    with st.expander("Zona berbahaya: hapus SEMUA data pemindahan"):
        st.warning(
            "Ini menghapus SELURUH record pemindahan (semua cabang, semua tanggal). "
            "Barang_master & riwayat cabang tidak ikut terhapus. Dipakai kalau mau "
            "buang total hasil backfill dari Excel dan ganti sumber lain (mis. hasil "
            "verifikasi manual ke Accurate) -- lihat PRD §11."
        )
        konfirmasi = st.text_input(
            "Ketik HAPUS SEMUA untuk konfirmasi", key="konfirmasi_hapus_semua"
        )
        if st.button("Hapus semua data pemindahan", type="secondary"):
            if konfirmasi == "HAPUS SEMUA":
                with db.get_conn() as conn:
                    db.hapus_semua_pemindahan(conn)
                st.success("Semua data pemindahan sudah dihapus.")
                st.rerun()
            else:
                st.error("Ketik persis 'HAPUS SEMUA' (huruf besar) untuk konfirmasi.")

# ---------------------------------------------------------------------------
# TAB 3 — Backfill batch
# ---------------------------------------------------------------------------
with tab_backfill:
    st.subheader("Import massal file Excel (backfill histori pemindahan)")
    st.caption(
        "Upload banyak file .xlsx sekaligus (Movement Stock / Daftar Stok, dideteksi "
        "otomatis per file). Baris dengan Receive/Masuk > 0 disimpan sebagai record "
        "pemindahan. Duplikat (fakta yang sama pernah tersimpan) otomatis di-skip."
    )

    uploaded_files = st.file_uploader(
        "File Excel (bisa banyak sekaligus)", type=["xlsx"], accept_multiple_files=True
    )

    if uploaded_files and st.button("Jalankan backfill"):
        progress = st.progress(0)
        status = st.empty()
        n_ok, n_fail, n_rows, n_dup_total = 0, 0, 0, 0
        fail_log = []

        with db.get_conn() as conn:
            for i, uf in enumerate(uploaded_files, 1):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                    tmp.write(uf.getbuffer())
                    tmp_path = tmp.name
                try:
                    parsed = parse_file(tmp_path)
                    n_kandidat = sum(1 for r in parsed.rows if r.qty_masuk > 0)
                    id_baru = db.simpan_pemindahan_dari_parsed(
                        conn, parsed, sumber="backfill_excel", file_asal=uf.name
                    )
                    n_baru = len(id_baru)
                    n_dup = n_kandidat - n_baru
                    db.log_import(
                        conn,
                        nama_file=uf.name,
                        cabang=parsed.cabang,
                        format_sumber=parsed.format_sumber,
                        tanggal_data=parsed.tanggal_awal,
                        n_baru=n_baru,
                        n_duplikat=n_dup,
                        konteks="backfill_tab",
                    )
                    n_rows += n_baru
                    n_dup_total += n_dup
                    n_ok += 1
                except Exception as e:
                    n_fail += 1
                    fail_log.append((uf.name, str(e)))
                finally:
                    os.unlink(tmp_path)

                progress.progress(i / len(uploaded_files))
                status.text(f"{i}/{len(uploaded_files)} file diproses...")

        st.success(
            f"Selesai. Sukses: {n_ok} file, Gagal: {n_fail} file. "
            f"Record baru: {n_rows}, duplikat (di-skip): {n_dup_total}"
        )
        if fail_log:
            st.error("File yang gagal:")
            for fn, err in fail_log:
                st.text(f"- {fn}: {err}")

# ---------------------------------------------------------------------------
# TAB 4 — Riwayat import
# ---------------------------------------------------------------------------
with tab_log:
    st.subheader("Riwayat file yang pernah diproses tool")
    st.caption(
        "Semua file yang pernah dibaca -- baik lewat backfill CLI, tab Backfill, "
        "maupun otomatis dari tab Pengecekan Harian."
    )
    with db.get_conn() as conn:
        log_rows = db.list_import_log(conn)
    if log_rows:
        df_log = pd.DataFrame(
            log_rows,
            columns=[
                "Waktu Diproses", "Nama File", "Cabang", "Format",
                "Tanggal Data", "Record Baru", "Duplikat", "Konteks",
            ],
        )
        st.dataframe(df_log, width='stretch', hide_index=True)
    else:
        st.info("Belum ada file yang diproses.")

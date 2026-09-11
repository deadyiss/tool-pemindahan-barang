import io
import math
import os
import tempfile
import time
import zipfile

import pandas as pd
import streamlit as st

import db
from parser import parse_file

st.set_page_config(page_title="Cek Pemindahan Barang", layout="wide")

TANGGAL_MINIMAL = "2026-08-01"
HALAMAN_SIZE = 50  

APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "ujangadmin")

JENIS_OPSI = ["pkp", "nonpkp", "keduanya"]
JENIS_LABEL = {"pkp": "PKP", "nonpkp": "Non-PKP", "keduanya": "Keduanya"}


def require_login():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if st.session_state.authenticated:
        return

    _, col_tengah, _ = st.columns([1, 1.2, 1])
    with col_tengah:
        with st.container(border=True):
            st.title("Login")
            st.caption("Masukkan username & password untuk akses.")
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


def render_tabel_hasil(rows: list[dict]):
    df = pd.DataFrame([
        {
            "SKU": r["sku"],
            "Nama Barang": r["nama"],
            "Qty Terjual": r["qty"],
            "Jenis": r["jenis"],
            "Valid?": "Ya" if r["valid"] else "Tidak",
            "Alasan": r["alasan"],
        }
        for r in rows
    ])
    st.dataframe(df, width="stretch", hide_index=True)


def notif_dan_rerun(jenis: str, pesan: str, tahan_detik: float = 3.0):
    getattr(st, jenis)(pesan)
    time.sleep(tahan_detik)
    st.rerun()


require_login()
db.init_db()

st.title("Tool Cek Validitas Barang — Penjualan Cabang")

tab_cek, tab_pemindahan, tab_penjualan, tab_jenis, tab_backfill, tab_log = st.tabs(
    [
        "Pengecekan Harian",
        "Kelola Data Pemindahan",
        "Kelola Data Penjualan",
        "Kelola Jenis Barang",
        "Backfill (Import Massal)",
        "Riwayat Import",
    ]
)

with tab_cek:
    st.subheader("Upload Excel penjualan cabang hari ini")
    st.caption(
        "Tool tampilkan barang yang terjual & valid/tidaknya (hover ikon mata untuk alasan). "
        "Format Movement Stock / Daftar Stok dideteksi otomatis."
    )
    uploaded = st.file_uploader("File Excel", type=["xlsx"], key="cek_upload")

    if "auto_capture_state" not in st.session_state:
        st.session_state.auto_capture_state = {}  

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

            n_masuk_kandidat = sum(1 for r in parsed.rows if r.qty_masuk > 0)
            file_key = uploaded.file_id
            state = st.session_state.auto_capture_state.get(file_key)

            status_text = (
                f"tersimpan ({state['n_baru']} record baru)" if state else "belum disimpan"
            )
            st.caption(
                f"Status histori pemindahan: **{status_text}** dari file ini "
                f"({n_masuk_kandidat} baris Receive/Masuk > 0 terdeteksi)."
            )
            bc1, bc2, _ = st.columns([1, 1, 3])
            with bc1:
                if st.button("Simpan ke Histori", disabled=(state is not None or n_masuk_kandidat == 0)):
                    with db.get_conn() as conn:
                        id_baru = db.simpan_pemindahan_dari_parsed(
                            conn, parsed, sumber="backfill_excel", file_asal=uploaded.name
                        )
                        n_baru = len(id_baru)
                        n_dup = n_masuk_kandidat - n_baru
                        db.log_import(
                            conn, nama_file=uploaded.name, cabang=parsed.cabang,
                            format_sumber=parsed.format_sumber, tanggal_data=parsed.tanggal_awal,
                            n_baru=n_baru, n_duplikat=n_dup, konteks="pengecekan_harian",
                        )
                    st.session_state.auto_capture_state[file_key] = {
                        "ids": id_baru, "n_baru": n_baru, "n_dup": n_dup,
                    }
                    notif_dan_rerun("success", f"Tersimpan: {n_baru} record baru, {n_dup} sudah ada sebelumnya (di-skip).")
            with bc2:
                if st.button("Batal Simpan", disabled=(state is None)):
                    with db.get_conn() as conn:
                        db.delete_pemindahan_batch(conn, state["ids"])
                    n_terhapus = len(state["ids"])
                    del st.session_state.auto_capture_state[file_key]
                    notif_dan_rerun("warning", f"{n_terhapus} record yang tadi tersimpan dari file ini sudah dihapus lagi.")

            terjual_rows = [r for r in parsed.rows if r.qty_terjual > 0]
            if not terjual_rows:
                st.info("Tidak ada barang dengan penjualan > 0 di file ini.")
            else:
                with db.get_conn() as conn:
                    hasil = []
                    for r in terjual_rows:
                        valid, alasan, jenis = db.cek_validitas_detail(
                            conn, parsed.format_sumber, parsed.cabang, r.sku, TANGGAL_MINIMAL
                        )
                        hasil.append({
                            "sku": r.sku, "nama": r.nama_barang, "qty": r.qty_terjual,
                            "jenis": jenis, "valid": valid, "alasan": alasan,
                        })
                        db.simpan_penjualan(
                            conn, format_sumber=parsed.format_sumber, sku=r.sku,
                            nama_barang=r.nama_barang, cabang=parsed.cabang,
                            tanggal=parsed.tanggal_awal, qty_terjual=r.qty_terjual,
                            sumber="upload_file", jenis_barang=jenis,
                        )

                n_valid = sum(1 for h in hasil if h["valid"])
                c1, c2, c3 = st.columns(3)
                c1.metric("Total barang terjual", len(hasil))
                c2.metric("Valid (boleh diinput)", n_valid)
                c3.metric("Tidak valid", len(hasil) - n_valid)

                render_tabel_hasil(hasil)

                df_download = pd.DataFrame([
                    {"SKU": h["sku"], "Nama Barang": h["nama"], "Qty Terjual": h["qty"],
                     "Jenis": h["jenis"], "Valid?": "Ya" if h["valid"] else "Tidak", "Alasan": h["alasan"]}
                    for h in hasil
                ])
                csv_data = df_download.to_csv(index=False).encode("utf-8")
                st.download_button("Download hasil (CSV)", csv_data, "hasil_cek.csv", "text/csv")

        os.unlink(tmp_path)

    st.divider()
    st.subheader("Cek cepat 1 barang (tanpa upload file)")
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
            barang_manual_pilih = st.selectbox("Barang", list(barang_manual_opsi.keys()), key="manual_cek_barang")
        with cc3:
            qty_manual = st.number_input("Qty terjual", min_value=0.0, step=1.0, key="manual_cek_qty")

        if st.button("Cek Validitas", key="manual_cek_button"):
            sku_m, nama_m = barang_manual_opsi[barang_manual_pilih]
            with db.get_conn() as conn:
                valid_m, alasan_m, jenis_m = db.cek_validitas_detail(
                    conn, fmt_manual, cabang_manual, sku_m, TANGGAL_MINIMAL
                )
                db.simpan_penjualan(
                    conn, format_sumber=fmt_manual, sku=sku_m, nama_barang=nama_m,
                    cabang=cabang_manual, tanggal=str(pd.Timestamp.now().date()),
                    qty_terjual=qty_manual, sumber="manual_cek", jenis_barang=jenis_m,
                )
            if valid_m:
                st.success(f"VALID -- {nama_m} ({jenis_m}). {alasan_m}")
            else:
                st.error(f"TIDAK VALID -- {nama_m} ({jenis_m}). {alasan_m}")

with tab_pemindahan:
    st.subheader("Tambah record pemindahan barang")
    st.caption("Input manual (tanpa file) -- untuk pemindahan yang belum sempat masuk lewat excel.")

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
            barang_pilih = st.selectbox("Barang", list(barang_opsi.keys()), key="add_barang")

        col3, col4 = st.columns(2)
        with col3:
            tanggal_pilih = st.date_input("Tanggal pemindahan", key="add_tanggal")
        with col4:
            qty_pilih = st.number_input("Qty", min_value=0.0, step=1.0, key="add_qty")

        if st.button("Tambah record"):
            sku, nama = barang_opsi[barang_pilih]
            with db.get_conn() as conn:
                id_baru = db.insert_pemindahan(
                    conn, format_sumber=fmt_cabang, sku=sku, nama_barang=nama,
                    cabang=cabang_pilih, tanggal_pindah=str(tanggal_pilih),
                    qty=qty_pilih, sumber="manual_input",
                )
            if id_baru is not None:
                notif_dan_rerun("success", f"Record ditambahkan: {nama} -> {cabang_pilih} ({tanggal_pilih})")
            else:
                notif_dan_rerun("warning", "Record identik sudah ada sebelumnya -- tidak disimpan dobel.")

    st.divider()
    st.subheader("Klasifikasi: barang masuk tanggal berapa saja")

    if daftar_cabang:
        cabang_klas = st.selectbox("Cabang", daftar_cabang, key="klas_cabang")
        with db.get_conn() as conn:
            tanggal_rows = db.list_tanggal_pemindahan(conn, cabang_klas)
        if tanggal_rows:
            df_tgl = pd.DataFrame(tanggal_rows, columns=["Tanggal", "Jumlah Barang", "Total Qty"])
            st.dataframe(df_tgl, width="stretch", hide_index=True)

            tanggal_opsi = [r[0] for r in tanggal_rows]
            dc1, dc2 = st.columns([2, 1])
            with dc1:
                tanggal_detail_pilih = st.selectbox("Pilih tanggal", tanggal_opsi, key="klas_tanggal_pilih")
            with dc2:
                st.write("")
                lihat_detail = st.button("Lihat Detail", key="klas_lihat_detail")

            if lihat_detail:
                with db.get_conn() as conn:
                    detail_rows = db.list_detail_tanggal(conn, cabang_klas, tanggal_detail_pilih)
                if detail_rows:
                    st.dataframe(pd.DataFrame(detail_rows, columns=["SKU", "Nama Barang", "Qty"]),
                                 width="stretch", hide_index=True)
                else:
                    st.info("Tidak ada rincian ditemukan.")
        else:
            st.info(f"Belum ada record pemindahan untuk {cabang_klas}.")

    st.divider()
    st.subheader("Data pemindahan tersimpan (edit/hapus — revisi nota)")

    fc1, fc2, fc3 = st.columns([1, 1, 2])
    with fc1:
        filter_cabang = st.selectbox("Filter cabang", ["(semua)"] + daftar_cabang, key="filter_cabang") if daftar_cabang else "(semua)"
    with fc2:
        if filter_cabang != "(semua)":
            with db.get_conn() as conn:
                tanggal_tersedia = [r[0] for r in db.list_tanggal_pemindahan(conn, filter_cabang)]
            filter_tanggal = st.selectbox("Filter tanggal", ["(semua)"] + tanggal_tersedia, key="filter_tanggal_pemindahan")
        else:
            filter_tanggal = "(semua)"
            st.selectbox("Filter tanggal", ["(pilih cabang dulu)"], disabled=True, key="filter_tanggal_disabled")
    with fc3:
        search_term = st.text_input("Cari nama barang / SKU", key="search_pemindahan")

    filter_key = f"{filter_cabang}|{filter_tanggal}|{search_term}"
    if st.session_state.get("_last_filter_key") != filter_key:
        st.session_state["_last_filter_key"] = filter_key
        st.session_state["crud_page"] = 0
    if "crud_page" not in st.session_state:
        st.session_state["crud_page"] = 0

    cabang_arg = None if filter_cabang == "(semua)" else filter_cabang
    tanggal_arg = None if filter_tanggal == "(semua)" else filter_tanggal
    search_arg = search_term.strip() or None

    with db.get_conn() as conn:
        total_rows = db.count_pemindahan(conn, cabang=cabang_arg, search=search_arg, tanggal=tanggal_arg)
    total_halaman = max(1, math.ceil(total_rows / HALAMAN_SIZE))
    st.session_state["crud_page"] = min(st.session_state["crud_page"], total_halaman - 1)

    with db.get_conn() as conn:
        rows = db.list_pemindahan(conn, cabang=cabang_arg, search=search_arg, tanggal=tanggal_arg,
                                    limit=HALAMAN_SIZE, offset=st.session_state["crud_page"] * HALAMAN_SIZE)

    if rows:
        df_rows = pd.DataFrame(rows, columns=["ID", "Cabang", "SKU", "Nama Barang", "Tanggal", "Qty", "Sumber"])
        st.dataframe(df_rows, width="stretch", hide_index=True)

        pc1, pc2, pc3 = st.columns([1, 2, 1])
        with pc1:
            if st.button("⬅ Sebelumnya", disabled=st.session_state["crud_page"] <= 0):
                st.session_state["crud_page"] -= 1
                st.rerun()
        with pc2:
            st.markdown(f"<div style='text-align:center'>Halaman {st.session_state['crud_page'] + 1} dari {total_halaman} — total {total_rows} record</div>", unsafe_allow_html=True)
        with pc3:
            if st.button("Berikutnya ➡", disabled=st.session_state["crud_page"] >= total_halaman - 1):
                st.session_state["crud_page"] += 1
                st.rerun()

        st.markdown("**Edit / Hapus record**")
        id_target = st.number_input("ID record", min_value=0, step=1, key="edit_id")
        ec1, ec2 = st.columns(2)
        with ec1:
            new_tanggal = st.date_input("Tanggal baru", key="edit_tanggal")
        with ec2:
            new_qty = st.number_input("Qty baru", min_value=0.0, step=1.0, key="edit_qty")
        colA, colB, _ = st.columns([1, 1, 5])
        with colA:
            if st.button("Update"):
                with db.get_conn() as conn:
                    db.update_pemindahan(conn, id_target, str(new_tanggal), new_qty)
                notif_dan_rerun("success", f"Record ID {id_target} diupdate.")
        with colB:
            if st.button("Hapus", type="secondary"):
                with db.get_conn() as conn:
                    db.delete_pemindahan(conn, id_target)
                notif_dan_rerun("success", f"Record ID {id_target} dihapus.")
    else:
        st.info("Tidak ada record yang cocok dengan filter/pencarian ini.")

    st.divider()
    with st.expander("Zona berbahaya: hapus SEMUA data pemindahan"):
        st.warning("Menghapus SELURUH record pemindahan. Barang_master & jenis barang tidak ikut terhapus.")
        konfirmasi = st.text_input("Ketik HAPUS SEMUA untuk konfirmasi", key="konfirmasi_hapus_semua")
        if st.button("Hapus semua data pemindahan", type="secondary"):
            if konfirmasi == "HAPUS SEMUA":
                with db.get_conn() as conn:
                    db.hapus_semua_pemindahan(conn)
                notif_dan_rerun("success", "Semua data pemindahan sudah dihapus.")
            else:
                st.error("Ketik persis 'HAPUS SEMUA' untuk konfirmasi.")

with tab_penjualan:
    st.subheader("Cari histori penjualan")
    st.caption("Setiap hasil pengecekan (upload file maupun cek manual) otomatis tersimpan di sini.")

    with db.get_conn() as conn:
        daftar_cabang_pj = db.list_cabang(conn)

    fp1, fp2 = st.columns(2)
    with fp1:
        cabang_pj = st.selectbox("Cabang", ["(semua)"] + daftar_cabang_pj, key="pj_cabang")
    with fp2:
        nama_pj = st.text_input("Nama barang", key="pj_nama")

    fp3, fp4, fp5 = st.columns(3)
    with fp3:
        sku_pj = st.text_input("SKU", key="pj_sku")
    with fp4:
        tgl_dari_pj = st.date_input("Dari tanggal", key="pj_tgl_dari", value=None)
    with fp5:
        tgl_sampai_pj = st.date_input("Sampai tanggal", key="pj_tgl_sampai", value=None)

    filter_key_pj = f"{cabang_pj}|{nama_pj}|{sku_pj}|{tgl_dari_pj}|{tgl_sampai_pj}"
    if st.session_state.get("_last_filter_key_pj") != filter_key_pj:
        st.session_state["_last_filter_key_pj"] = filter_key_pj
        st.session_state["pj_page"] = 0
    if "pj_page" not in st.session_state:
        st.session_state["pj_page"] = 0

    args = dict(
        cabang=None if cabang_pj == "(semua)" else cabang_pj,
        sku=sku_pj.strip() or None,
        nama=nama_pj.strip() or None,
        tanggal_dari=str(tgl_dari_pj) if tgl_dari_pj else None,
        tanggal_sampai=str(tgl_sampai_pj) if tgl_sampai_pj else None,
    )

    with db.get_conn() as conn:
        total_pj = db.count_penjualan(conn, **args)
    total_halaman_pj = max(1, math.ceil(total_pj / HALAMAN_SIZE))
    st.session_state["pj_page"] = min(st.session_state["pj_page"], total_halaman_pj - 1)

    with db.get_conn() as conn:
        rows_pj = db.search_penjualan(conn, limit=HALAMAN_SIZE, offset=st.session_state["pj_page"] * HALAMAN_SIZE, **args)

    if rows_pj:
        df_pj = pd.DataFrame(rows_pj, columns=["ID", "Tanggal", "Cabang", "SKU", "Nama Barang", "Qty Terjual", "Sumber", "Jenis"])
        df_pj["Jenis"] = df_pj["Jenis"].fillna("Belum diklasifikasikan")
        st.dataframe(df_pj, width="stretch", hide_index=True)

        ppc1, ppc2, ppc3 = st.columns([1, 2, 1])
        with ppc1:
            if st.button("⬅ Sebelumnya", key="pj_prev", disabled=st.session_state["pj_page"] <= 0):
                st.session_state["pj_page"] -= 1
                st.rerun()
        with ppc2:
            st.markdown(f"<div style='text-align:center'>Halaman {st.session_state['pj_page'] + 1} dari {total_halaman_pj} — total {total_pj} record</div>", unsafe_allow_html=True)
        with ppc3:
            if st.button("Berikutnya ➡", key="pj_next", disabled=st.session_state["pj_page"] >= total_halaman_pj - 1):
                st.session_state["pj_page"] += 1
                st.rerun()

        csv_pj = df_pj.to_csv(index=False).encode("utf-8")
        st.download_button("Download hasil pencarian (CSV)", csv_pj, "histori_penjualan.csv", "text/csv")

        st.markdown("**Edit / Hapus record penjualan**")
        id_target_pj = st.number_input("ID record", min_value=0, step=1, key="pj_edit_id")
        epc1, epc2 = st.columns(2)
        with epc1:
            new_tanggal_pj = st.date_input("Tanggal baru", key="pj_edit_tanggal")
        with epc2:
            new_qty_pj = st.number_input("Qty baru", min_value=0.0, step=1.0, key="pj_edit_qty")
        epcA, epcB, _ = st.columns([1, 1, 5])
        with epcA:
            if st.button("Update", key="pj_update_btn"):
                with db.get_conn() as conn:
                    db.update_penjualan(conn, id_target_pj, str(new_tanggal_pj), new_qty_pj)
                notif_dan_rerun("success", f"Record penjualan ID {id_target_pj} diupdate.")
        with epcB:
            if st.button("Hapus", key="pj_hapus_btn", type="secondary"):
                with db.get_conn() as conn:
                    db.delete_penjualan(conn, id_target_pj)
                notif_dan_rerun("success", f"Record penjualan ID {id_target_pj} dihapus.")
    else:
        st.info("Tidak ada histori penjualan yang cocok dengan filter ini.")

with tab_jenis:
    st.subheader("Tambah / ubah jenis barang")
    st.caption("Tiap perubahan tercatat sebagai riwayat (tidak menimpa histori lama).")

    with db.get_conn() as conn:
        daftar_cabang_jb = db.list_cabang(conn)

    if not daftar_cabang_jb:
        st.info("Belum ada data cabang. Lakukan backfill dulu.")
    else:
        jc1, jc2, jc3 = st.columns([1, 2, 1])
        with jc1:
            cabang_jb = st.selectbox("Cabang (menentukan format katalog)", daftar_cabang_jb, key="jb_cabang")
        with db.get_conn() as conn:
            fmt_jb = db.get_format_cabang(conn, cabang_jb)
            barang_jb_list = db.list_barang(conn, fmt_jb) if fmt_jb else []
        barang_jb_opsi = {f"{sku} — {nama}": (sku, nama) for sku, nama in barang_jb_list}
        with jc2:
            barang_jb_pilih = st.selectbox("Barang", list(barang_jb_opsi.keys()), key="jb_barang")
        with jc3:
            jenis_jb_pilih = st.selectbox("Jenis", JENIS_OPSI, format_func=lambda j: JENIS_LABEL[j], key="jb_jenis")

        if st.button("Simpan Jenis Barang"):
            sku_jb, nama_jb = barang_jb_opsi[barang_jb_pilih]
            with db.get_conn() as conn:
                db.set_jenis_barang(conn, fmt_jb, sku_jb, jenis_jb_pilih, sumber="manual_admin")
            notif_dan_rerun("success", f"{nama_jb} sekarang berstatus {JENIS_LABEL[jenis_jb_pilih]}.")

        with st.expander("Lihat riwayat perubahan jenis barang ini"):
            sku_jb_cek, _ = barang_jb_opsi[barang_jb_pilih]
            with db.get_conn() as conn:
                riwayat = db.list_riwayat_jenis_barang(conn, fmt_jb, sku_jb_cek)
            if riwayat:
                st.dataframe(pd.DataFrame(riwayat, columns=["Jenis", "Sumber", "Waktu Ubah"]), width="stretch", hide_index=True)
            else:
                st.caption("Belum ada riwayat untuk barang ini.")

    st.divider()
    st.subheader("Daftar barang & status jenisnya")

    jf1, jf2, jf3 = st.columns(3)
    with jf1:
        filter_fmt = st.selectbox("Format", ["(semua)", "movement", "daftar"], key="jb_filter_fmt")
    with jf2:
        filter_jenis_opsi = ["(semua)", "belum"] + JENIS_OPSI
        filter_jenis_pilih = st.selectbox(
            "Status jenis", filter_jenis_opsi,
            format_func=lambda j: {"(semua)": "(semua)", "belum": "Belum diklasifikasikan"}.get(j, JENIS_LABEL.get(j, j)),
            key="jb_filter_jenis",
        )
    with jf3:
        search_jb = st.text_input("Cari nama/SKU", key="jb_search")

    filter_key_jb = f"{filter_fmt}|{filter_jenis_pilih}|{search_jb}"
    if st.session_state.get("_last_filter_key_jb") != filter_key_jb:
        st.session_state["_last_filter_key_jb"] = filter_key_jb
        st.session_state["jb_page"] = 0
    if "jb_page" not in st.session_state:
        st.session_state["jb_page"] = 0

    args_jb = dict(
        format_sumber=None if filter_fmt == "(semua)" else filter_fmt,
        filter_jenis=None if filter_jenis_pilih == "(semua)" else filter_jenis_pilih,
        search=search_jb.strip() or None,
    )
    with db.get_conn() as conn:
        total_jb = db.count_barang_dengan_jenis(conn, **args_jb)
    total_halaman_jb = max(1, math.ceil(total_jb / HALAMAN_SIZE))
    st.session_state["jb_page"] = min(st.session_state["jb_page"], total_halaman_jb - 1)

    with db.get_conn() as conn:
        rows_jb = db.list_barang_dengan_jenis(conn, limit=HALAMAN_SIZE, offset=st.session_state["jb_page"] * HALAMAN_SIZE, **args_jb)

    if rows_jb:
        df_jb_asli = pd.DataFrame(rows_jb, columns=["Format", "SKU", "Nama Barang", "Jenis"])
        df_jb_tampil = df_jb_asli.copy()
        df_jb_tampil["Jenis"] = df_jb_tampil["Jenis"].map(lambda j: JENIS_LABEL.get(j, "Belum diklasifikasikan"))

        label_ke_kode = {v: k for k, v in JENIS_LABEL.items()}
        st.caption("Ubah langsung kolom 'Jenis' di tabel, lalu klik 'Simpan Perubahan Jenis'.")
        df_diedit = st.data_editor(
            df_jb_tampil,
            width="stretch",
            hide_index=True,
            disabled=["Format", "SKU", "Nama Barang"],
            column_config={
                "Jenis": st.column_config.SelectboxColumn(
                    "Jenis", options=list(JENIS_LABEL.values()) + ["Belum diklasifikasikan"]
                )
            },
            key="jb_editor",
        )

        if st.button("Simpan Perubahan Jenis"):
            n_ubah = 0
            with db.get_conn() as conn:
                for i in range(len(df_jb_tampil)):
                    label_lama = df_jb_tampil.iloc[i]["Jenis"]
                    label_baru = df_diedit.iloc[i]["Jenis"]
                    kode_baru = label_ke_kode.get(label_baru)
                    if label_baru != label_lama and kode_baru in JENIS_OPSI:
                        db.set_jenis_barang(
                            conn, df_jb_asli.iloc[i]["Format"], df_jb_asli.iloc[i]["SKU"],
                            kode_baru, sumber="manual_admin_tabel",
                        )
                        n_ubah += 1
            notif_dan_rerun("success", f"{n_ubah} barang diperbarui." if n_ubah else "Tidak ada perubahan.")

        jpc1, jpc2, jpc3 = st.columns([1, 2, 1])
        with jpc1:
            if st.button("⬅ Sebelumnya", key="jb_prev", disabled=st.session_state["jb_page"] <= 0):
                st.session_state["jb_page"] -= 1
                st.rerun()
        with jpc2:
            st.markdown(f"<div style='text-align:center'>Halaman {st.session_state['jb_page'] + 1} dari {total_halaman_jb} — total {total_jb} barang</div>", unsafe_allow_html=True)
        with jpc3:
            if st.button("Berikutnya ➡", key="jb_next", disabled=st.session_state["jb_page"] >= total_halaman_jb - 1):
                st.session_state["jb_page"] += 1
                st.rerun()
    else:
        st.info("Tidak ada barang yang cocok dengan filter ini.")

with tab_backfill:
    st.subheader("Import massal file Excel")
    st.caption(
        "Upload banyak file .xlsx, atau 1 file .zip berisi banyak file (boleh folder "
        "bertingkat) -- kompres dulu folder Excel-mu jadi .zip kalau mau upload sekaligus."
    )

    uploaded_files = st.file_uploader("File Excel (.xlsx) atau ZIP", type=["xlsx", "zip"], accept_multiple_files=True)

    def _expand_uploads(files):
        items = []
        for uf in files:
            if uf.name.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(uf.getbuffer())) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        fname = info.filename
                        base = os.path.basename(fname)
                        if not fname.lower().endswith(".xlsx"):
                            continue
                        if "__MACOSX" in fname or base.startswith("._") or base.startswith("~$"):
                            continue
                        items.append((fname, zf.read(info.filename)))
            else:
                items.append((uf.name, uf.getbuffer()))
        return items

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        jalankan_backfill = st.button("Jalankan Backfill (simpan pemindahan)")
    with bcol2:
        jalankan_sync = st.button("Sinkronkan Katalog Lengkap (semua barang, lebih lambat)")
        st.caption("Jalankan sesekali saja -- perlu supaya dropdown barang & klasifikasi PKP/Non lebih lengkap.")

    if uploaded_files and (jalankan_backfill or jalankan_sync):
        xlsx_items = _expand_uploads(uploaded_files)
        if not xlsx_items:
            st.warning("Tidak ada file .xlsx ditemukan.")
        else:
            st.caption(f"Total {len(xlsx_items)} file .xlsx akan diproses.")
            progress = st.progress(0)
            status = st.empty()
            n_ok, n_fail, n_rows, n_dup_total = 0, 0, 0, 0
            fail_log = []

            with db.get_conn() as conn:
                for i, (nama_file, content) in enumerate(xlsx_items, 1):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                        tmp.write(content)
                        tmp_path = tmp.name
                    try:
                        parsed = parse_file(tmp_path)
                        if jalankan_backfill:
                            n_kandidat = sum(1 for r in parsed.rows if r.qty_masuk > 0)
                            id_baru = db.simpan_pemindahan_dari_parsed(conn, parsed, sumber="backfill_excel", file_asal=nama_file)
                            n_baru = len(id_baru)
                            n_dup = n_kandidat - n_baru
                            db.log_import(conn, nama_file=nama_file, cabang=parsed.cabang,
                                           format_sumber=parsed.format_sumber, tanggal_data=parsed.tanggal_awal,
                                           n_baru=n_baru, n_duplikat=n_dup, konteks="backfill_tab")
                            n_rows += n_baru
                            n_dup_total += n_dup
                        else:
                            n_rows += db.sinkronkan_katalog_lengkap(conn, parsed)
                        n_ok += 1
                    except Exception as e:
                        n_fail += 1
                        fail_log.append((nama_file, str(e)))
                    finally:
                        os.unlink(tmp_path)
                    progress.progress(i / len(xlsx_items))
                    status.text(f"{i}/{len(xlsx_items)} file diproses...")

            if jalankan_backfill:
                st.success(f"Selesai. Sukses: {n_ok}, Gagal: {n_fail}. Record baru: {n_rows}, duplikat: {n_dup_total}")
            else:
                st.success(f"Selesai. Sukses: {n_ok}, Gagal: {n_fail}. Baris katalog di-upsert: {n_rows}")
            if fail_log:
                st.error("File yang gagal:")
                for fn, err in fail_log:
                    st.text(f"- {fn}: {err}")

with tab_log:
    st.subheader("Riwayat file yang pernah diproses")
    with db.get_conn() as conn:
        log_rows = db.list_import_log(conn)
    if log_rows:
        df_log = pd.DataFrame(log_rows, columns=["Waktu", "Nama File", "Cabang", "Format", "Tanggal Data", "Baru", "Duplikat", "Konteks"])
        st.dataframe(df_log, width="stretch", hide_index=True)
    else:
        st.info("Belum ada file yang diproses.")
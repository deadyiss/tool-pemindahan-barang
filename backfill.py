"""
Backfill batch: baca semua file .xlsx di sebuah folder (rekursif),
ekstrak baris qty_masuk > 0, insert ke tabel pemindahan_barang.

Pemakaian:
    python backfill.py /path/ke/folder/Agustus
"""
import os
import sys

from db import get_conn, init_db, log_import, simpan_pemindahan_dari_parsed
from parser import parse_file


def run_backfill(folder: str, db_path: str = "pemindahan.db"):
    init_db(db_path)

    xlsx_files = []
    for dirpath, _, filenames in os.walk(folder):
        for fn in filenames:
            if fn.lower().endswith(".xlsx"):
                xlsx_files.append(os.path.join(dirpath, fn))

    print(f"Ditemukan {len(xlsx_files)} file .xlsx")

    n_ok, n_fail, n_rows_inserted, n_duplikat = 0, 0, 0, 0
    fail_log = []

    with get_conn(db_path) as conn:
        for i, fp in enumerate(xlsx_files, 1):
            try:
                parsed = parse_file(fp)
            except Exception as e:
                n_fail += 1
                fail_log.append((fp, str(e)))
                continue

            n_masuk_kandidat = sum(1 for r in parsed.rows if r.qty_masuk > 0)
            id_baru = simpan_pemindahan_dari_parsed(
                conn, parsed, sumber="backfill_excel", file_asal=os.path.basename(fp)
            )
            n_baru = len(id_baru)
            n_dup_file = n_masuk_kandidat - n_baru
            log_import(
                conn,
                nama_file=os.path.basename(fp),
                cabang=parsed.cabang,
                format_sumber=parsed.format_sumber,
                tanggal_data=parsed.tanggal_awal,
                n_baru=n_baru,
                n_duplikat=n_dup_file,
                konteks="backfill_cli",
            )
            n_rows_inserted += n_baru
            n_duplikat += n_dup_file
            n_ok += 1

            if i % 100 == 0:
                print(f"  ...{i}/{len(xlsx_files)} file diproses")

    print(f"\nSelesai. Sukses: {n_ok} file, Gagal: {n_fail} file.")
    print(f"Record pemindahan baru tersimpan: {n_rows_inserted}")
    print(f"Record duplikat (di-skip, sudah ada sebelumnya): {n_duplikat}")
    if fail_log:
        print("\nFile yang gagal diparse:")
        for fp, err in fail_log[:20]:
            print(f"  - {fp}: {err}")
        if len(fail_log) > 20:
            print(f"  ...dan {len(fail_log) - 20} file lainnya")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Pemakaian: python backfill.py <folder>")
        sys.exit(1)
    run_backfill(sys.argv[1])

"""
Sinkronkan katalog barang LENGKAP (semua baris di tiap file, bukan cuma yang
Receive/Masuk > 0) -- jalan lebih lambat dari backfill.py biasa, makanya
dipisah jadi script sendiri, dijalankan SESEKALI saja saat butuh katalog
lengkap (mis. sebelum seed klasifikasi PKP/Non-PKP/Keduanya, supaya lebih
banyak barang di CSV klasifikasi bisa ketemu pasangannya).

Tidak menyentuh data pemindahan sama sekali -- aman dijalankan kapan saja,
tidak akan bikin data pemindahan dobel.

Pemakaian:
    python sync_katalog.py /path/ke/folder/Agustus
"""
import os
import sys

from db import get_conn, init_db, sinkronkan_katalog_lengkap
from parser import parse_file


def run_sync(folder: str, db_path: str = "pemindahan.db"):
    init_db(db_path)

    xlsx_files = []
    for dirpath, _, filenames in os.walk(folder):
        for fn in filenames:
            if fn.lower().endswith(".xlsx"):
                xlsx_files.append(os.path.join(dirpath, fn))

    print(f"Ditemukan {len(xlsx_files)} file .xlsx")

    n_ok, n_fail, total_baris = 0, 0, 0
    fail_log = []

    with get_conn(db_path) as conn:
        for i, fp in enumerate(xlsx_files, 1):
            try:
                parsed = parse_file(fp)
            except Exception as e:
                n_fail += 1
                fail_log.append((fp, str(e)))
                continue
            total_baris += sinkronkan_katalog_lengkap(conn, parsed)
            n_ok += 1
            if i % 50 == 0:
                print(f"  ...{i}/{len(xlsx_files)} file diproses")

    print(f"\nSelesai. Sukses: {n_ok} file, Gagal: {n_fail} file.")
    print(f"Total baris barang di-upsert ke katalog: {total_baris}")
    if fail_log:
        print("\nFile yang gagal:")
        for fp, err in fail_log[:20]:
            print(f"  - {fp}: {err}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Pemakaian: python sync_katalog.py <folder>")
        sys.exit(1)
    run_sync(sys.argv[1])

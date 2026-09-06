"""
Seed satu-kali: load klasifikasi PKP / Non-PKP / Keduanya dari 3 file CSV
client ke tabel jenis_barang_riwayat.

Strategi matching (sudah diverifikasi manual sebelum ditulis di sini -- lihat
diskusi PRD soal ini):
- nonpkp.csv & kolom NonPKP di keduanya.csv: SKU-nya pakai skema yang SAMA
  dengan barang_master (Movement Stock/Daftar Stok) -> match by SKU langsung.
- pkp.csv: SKU-nya pakai skema BERBEDA (kode internal modul PKP Accurate,
  mis. "CKP-1156") yang tidak match ke skema barang_master sama sekali.
  Penamaan barangnya juga beda konvensi (ada pengali kemasan "12x", urutan
  kata beda, dll) -- terbukti cuma ~8% bisa dicocokkan otomatis walau sudah
  dicoba normalisasi. SISANYA SENGAJA TIDAK DIPROSES DI SINI -- akan
  diklasifikasikan manual oleh admin lewat tab "Kelola Jenis Barang"
  (muncul otomatis di filter "belum diklasifikasikan").

Pemakaian:
    python seed_jenis_barang.py /path/ke/pkp.csv /path/ke/nonpkp.csv /path/ke/keduanya.csv
"""
import csv
import re
import sys

from db import get_conn, init_db, set_jenis_barang


def _load_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _norm_tokens(s: str) -> tuple:
    s = s.lower()
    s = re.sub(r"\d+\s*x\s*\d*", " ", s)
    s = re.sub(r"\b\d+\.", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return tuple(sorted(t for t in s.split() if t))


def seed(pkp_path: str, nonpkp_path: str, keduanya_path: str, db_path: str = "pemindahan.db"):
    init_db(db_path)
    with get_conn(db_path) as conn:
        barang = conn.execute("SELECT format_sumber, sku, nama_barang FROM barang_master").fetchall()

    sku_map = {}       # sku upper -> [(format_sumber, sku_asli)]
    nama_sig_map = {}  # signature token -> [(format_sumber, sku_asli)]
    for fmt, sku, nama in barang:
        sku_map.setdefault(sku.strip().upper(), []).append((fmt, sku))
        nama_sig_map.setdefault(_norm_tokens(nama), []).append((fmt, sku))

    stat = {"nonpkp_ok": 0, "nonpkp_skip": 0, "keduanya_ok": 0, "keduanya_skip": 0,
            "pkp_ok": 0, "pkp_skip": 0}

    with get_conn(db_path) as conn:
        # --- NONPKP: match by SKU ---
        for r in _load_csv(nonpkp_path):
            sku = r["No. Barang"].strip().upper()
            cands = sku_map.get(sku)
            if cands:
                for fmt, sku_asli in cands:
                    set_jenis_barang(conn, fmt, sku_asli, "nonpkp", sumber="seed_csv")
                stat["nonpkp_ok"] += 1
            else:
                stat["nonpkp_skip"] += 1

        # --- KEDUANYA: match by SKU kolom NonPKP ---
        for r in _load_csv(keduanya_path):
            sku = r["No. Barang NonPKP"].strip().upper()
            cands = sku_map.get(sku)
            if cands:
                for fmt, sku_asli in cands:
                    set_jenis_barang(conn, fmt, sku_asli, "keduanya", sumber="seed_csv")
                stat["keduanya_ok"] += 1
            else:
                stat["keduanya_skip"] += 1

        # --- PKP: match by SKU dulu (jaga-jaga), lalu fallback nama (token) ---
        for r in _load_csv(pkp_path):
            sku = r["No. Barang"].strip().upper()
            nama = r["Keterangan"]
            cands = sku_map.get(sku) or nama_sig_map.get(_norm_tokens(nama))
            if cands:
                for fmt, sku_asli in cands:
                    set_jenis_barang(conn, fmt, sku_asli, "pkp", sumber="seed_csv")
                stat["pkp_ok"] += 1
            else:
                stat["pkp_skip"] += 1

    print("Selesai seed klasifikasi jenis barang.")
    print(f"  NonPKP   : {stat['nonpkp_ok']} berhasil, {stat['nonpkp_skip']} dilewati (SKU tidak ketemu di barang_master)")
    print(f"  Keduanya : {stat['keduanya_ok']} berhasil, {stat['keduanya_skip']} dilewati")
    print(f"  PKP      : {stat['pkp_ok']} berhasil, {stat['pkp_skip']} dilewati (butuh klasifikasi manual)")
    print("\nSisa yang dilewati akan muncul di filter 'Belum diklasifikasikan' di tab Kelola Jenis Barang.")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Pemakaian: python seed_jenis_barang.py <pkp.csv> <nonpkp.csv> <keduanya.csv>")
        sys.exit(1)
    seed(sys.argv[1], sys.argv[2], sys.argv[3])

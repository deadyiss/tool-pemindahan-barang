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

    sku_map = {}       
    nama_sig_map = {}  
    for fmt, sku, nama in barang:
        sku_map.setdefault(sku.strip().upper(), []).append((fmt, sku))
        nama_sig_map.setdefault(_norm_tokens(nama), []).append((fmt, sku))

    stat = {"nonpkp_ok": 0, "nonpkp_skip": 0, "keduanya_ok": 0, "keduanya_skip": 0,
            "pkp_ok": 0, "pkp_skip": 0}

    with get_conn(db_path) as conn:
        for r in _load_csv(nonpkp_path):
            sku = r["No. Barang"].strip().upper()
            cands = sku_map.get(sku)
            if cands:
                for fmt, sku_asli in cands:
                    set_jenis_barang(conn, fmt, sku_asli, "nonpkp", sumber="seed_csv")
                stat["nonpkp_ok"] += 1
            else:
                stat["nonpkp_skip"] += 1

        for r in _load_csv(keduanya_path):
            sku = r["No. Barang NonPKP"].strip().upper()
            cands = sku_map.get(sku)
            if cands:
                for fmt, sku_asli in cands:
                    set_jenis_barang(conn, fmt, sku_asli, "keduanya", sumber="seed_csv")
                stat["keduanya_ok"] += 1
            else:
                stat["keduanya_skip"] += 1

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

"""
Skema database & helper query untuk tool cek pemindahan barang.
Lihat PRD §5 untuk desain skema.

Koneksi database: pakai Turso (cloud, persisten -- lihat PRD soal kenapa SQLite
lokal TIDAK aman dipakai di web-deploy gratis) kalau env var TURSO_DATABASE_URL
& TURSO_AUTH_TOKEN sudah diset. Kalau belum diset (mis. waktu development di
komputer sendiri), otomatis fallback ke file SQLite lokal -- supaya tetap bisa
dites tanpa harus punya akun Turso dulu.
"""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = "pemindahan.db"  # dipakai kalau TURSO_DATABASE_URL tidak diset (mode lokal/dev)

SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS cabang_master (
        cabang TEXT PRIMARY KEY,
        format_sumber TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS barang_master (
        format_sumber TEXT NOT NULL,
        sku TEXT NOT NULL,
        nama_barang TEXT NOT NULL,
        category TEXT,
        PRIMARY KEY (format_sumber, sku)
    )""",
    """CREATE TABLE IF NOT EXISTS pemindahan_barang (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        format_sumber TEXT NOT NULL,
        sku TEXT NOT NULL,
        nama_barang TEXT NOT NULL,
        cabang TEXT NOT NULL,
        tanggal_pindah TEXT NOT NULL,
        qty REAL NOT NULL,
        sumber TEXT NOT NULL,
        file_asal TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS idx_pemindahan_lookup
        ON pemindahan_barang (cabang, sku, tanggal_pindah)""",
    # Cegah duplikat: fakta yang sama (barang+cabang+tanggal+qty) tidak boleh
    # tercatat dua kali, walau berasal dari upload/file berbeda.
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_pemindahan_dedup
        ON pemindahan_barang (cabang, sku, tanggal_pindah, qty)""",
    # Riwayat tiap file yang pernah diproses tool.
    """CREATE TABLE IF NOT EXISTS import_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nama_file TEXT NOT NULL,
        cabang TEXT,
        format_sumber TEXT,
        tanggal_data TEXT,
        n_baru INTEGER NOT NULL,
        n_duplikat INTEGER NOT NULL,
        konteks TEXT NOT NULL,
        waktu_import TEXT NOT NULL
    )""",
]


def _is_turso_configured() -> bool:
    return bool(os.environ.get("TURSO_DATABASE_URL")) and bool(os.environ.get("TURSO_AUTH_TOKEN"))


@contextmanager
def get_conn(db_path: str = DB_PATH):
    """Buka koneksi ke Turso (kalau env var-nya ada) atau ke file SQLite lokal
    (fallback dev/testing). Dipanggil sebagai context manager: `with get_conn() as conn:`."""
    if _is_turso_configured():
        import libsql  # hanya di-import kalau memang dipakai -- tidak wajib ada saat dev lokal

        conn = libsql.connect(
            database=os.environ["TURSO_DATABASE_URL"],
            auth_token=os.environ["TURSO_AUTH_TOKEN"],
        )
    else:
        conn = sqlite3.connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str = DB_PATH):
    with get_conn(db_path) as conn:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)


def upsert_cabang(conn, cabang: str, format_sumber: str):
    conn.execute(
        """INSERT INTO cabang_master (cabang, format_sumber)
           VALUES (?, ?)
           ON CONFLICT(cabang) DO UPDATE SET format_sumber = excluded.format_sumber""",
        (cabang, format_sumber),
    )


def upsert_barang(conn, format_sumber: str, sku: str, nama_barang: str, category: str | None):
    conn.execute(
        """INSERT INTO barang_master (format_sumber, sku, nama_barang, category)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(format_sumber, sku) DO UPDATE SET
               nama_barang = excluded.nama_barang,
               category = excluded.category""",
        (format_sumber, sku, nama_barang, category),
    )


def insert_pemindahan(
    conn,
    format_sumber: str,
    sku: str,
    nama_barang: str,
    cabang: str,
    tanggal_pindah: str,
    qty: float,
    sumber: str,
    file_asal: str | None = None,
) -> int | None:
    """Insert 1 record pemindahan. Return id record kalau benar-benar disimpan,
    None kalau di-skip karena sudah ada record identik (cabang+sku+tanggal+qty)
    -- lihat uq_pemindahan_dedup di SCHEMA."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO pemindahan_barang
           (format_sumber, sku, nama_barang, cabang, tanggal_pindah, qty, sumber, file_asal)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (format_sumber, sku, nama_barang, cabang, tanggal_pindah, qty, sumber, file_asal),
    )
    if cur.rowcount <= 0:
        return None
    row = conn.execute(
        """SELECT id FROM pemindahan_barang
           WHERE cabang = ? AND sku = ? AND tanggal_pindah = ? AND qty = ?""",
        (cabang, sku, tanggal_pindah, qty),
    ).fetchone()
    return row[0] if row else None


def simpan_pemindahan_dari_parsed(conn, parsed, sumber: str, file_asal: str | None = None) -> list[int]:
    """Helper terpusat: dari hasil parser.parse_file(), upsert cabang & barang_master,
    lalu simpan tiap baris dengan qty_masuk > 0 sebagai record pemindahan (dedup
    otomatis lewat insert_pemindahan). Dipakai oleh backfill.py, tab Backfill,
    DAN tab Pengecekan Harian (auto-capture) -- satu logika, tiga pemakai.
    Return: daftar id record BARU yang benar-benar tersimpan (bukan duplikat) --
    dipakai buat bisa 'undo' (hapus lagi) kalau checkbox auto-capture dimatikan."""
    upsert_cabang(conn, parsed.cabang, parsed.format_sumber)
    id_baru = []
    for row in parsed.rows:
        upsert_barang(conn, parsed.format_sumber, row.sku, row.nama_barang, row.category)
        if row.qty_masuk > 0:
            id_ = insert_pemindahan(
                conn,
                format_sumber=parsed.format_sumber,
                sku=row.sku,
                nama_barang=row.nama_barang,
                cabang=parsed.cabang,
                tanggal_pindah=parsed.tanggal_awal,
                qty=row.qty_masuk,
                sumber=sumber,
                file_asal=file_asal,
            )
            if id_ is not None:
                id_baru.append(id_)
    return id_baru


def cek_validitas(conn, cabang: str, sku: str, tanggal_minimal: str = "2026-08-01") -> bool:
    """True kalau barang pernah dipindahkan ke cabang ini sejak tanggal_minimal."""
    row = conn.execute(
        """SELECT 1 FROM pemindahan_barang
           WHERE cabang = ? AND sku = ? AND tanggal_pindah >= ?
           LIMIT 1""",
        (cabang, sku, tanggal_minimal),
    ).fetchone()
    return row is not None


def get_format_cabang(conn, cabang: str) -> str | None:
    row = conn.execute(
        "SELECT format_sumber FROM cabang_master WHERE cabang = ?", (cabang,)
    ).fetchone()
    return row[0] if row else None


def list_barang(conn, format_sumber: str):
    return conn.execute(
        """SELECT sku, nama_barang FROM barang_master
           WHERE format_sumber = ? ORDER BY nama_barang""",
        (format_sumber,),
    ).fetchall()


def list_cabang(conn):
    return [r[0] for r in conn.execute("SELECT cabang FROM cabang_master ORDER BY cabang").fetchall()]


def list_pemindahan(
    conn,
    cabang: str | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
):
    """Ambil record pemindahan dengan filter cabang & pencarian nama/SKU,
    plus pagination (limit+offset) -- supaya data yang jumlahnya ribuan tetap
    bisa di-scroll semua lewat 'halaman berikutnya', bukan cuma 200 pertama."""
    where = []
    params = []
    if cabang:
        where.append("cabang = ?")
        params.append(cabang)
    if search:
        where.append("(nama_barang LIKE ? OR sku LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.extend([limit, offset])
    return conn.execute(
        f"""SELECT id, cabang, sku, nama_barang, tanggal_pindah, qty, sumber
            FROM pemindahan_barang {where_sql}
            ORDER BY tanggal_pindah DESC, id DESC LIMIT ? OFFSET ?""",
        tuple(params),
    ).fetchall()


def count_pemindahan(conn, cabang: str | None = None, search: str | None = None) -> int:
    where = []
    params = []
    if cabang:
        where.append("cabang = ?")
        params.append(cabang)
    if search:
        where.append("(nama_barang LIKE ? OR sku LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    row = conn.execute(
        f"SELECT COUNT(*) FROM pemindahan_barang {where_sql}", tuple(params)
    ).fetchone()
    return row[0] if row else 0


def delete_pemindahan(conn, id_: int):
    conn.execute("DELETE FROM pemindahan_barang WHERE id = ?", (id_,))


def delete_pemindahan_batch(conn, id_list: list[int]):
    """Hapus banyak record sekaligus berdasarkan ID -- dipakai buat 'undo'
    auto-capture waktu checkbox 'simpan ke histori' dimatikan lagi setelah
    sempat dicentang (lihat app.py tab Pengecekan Harian)."""
    if not id_list:
        return
    placeholders = ",".join("?" for _ in id_list)
    conn.execute(f"DELETE FROM pemindahan_barang WHERE id IN ({placeholders})", tuple(id_list))


def update_pemindahan(conn, id_: int, tanggal_pindah: str, qty: float):
    conn.execute(
        "UPDATE pemindahan_barang SET tanggal_pindah = ?, qty = ? WHERE id = ?",
        (tanggal_pindah, qty, id_),
    )


def list_tanggal_pemindahan(conn, cabang: str):
    """Daftar tanggal unik yang punya record pemindahan untuk 1 cabang,
    beserta jumlah barang -- dipakai fitur 'klasifikasi barang masuk per
    tanggal' di tab Kelola Data Pemindahan."""
    return conn.execute(
        """SELECT tanggal_pindah, COUNT(*) AS jumlah_barang, SUM(qty) AS total_qty
           FROM pemindahan_barang WHERE cabang = ?
           GROUP BY tanggal_pindah ORDER BY tanggal_pindah DESC""",
        (cabang,),
    ).fetchall()


def list_detail_tanggal(conn, cabang: str, tanggal: str):
    """Rincian barang (SKU, nama, qty) untuk 1 cabang di 1 tanggal spesifik --
    dipanggil setelah user pilih tanggal & klik 'Lihat Detail'."""
    return conn.execute(
        """SELECT sku, nama_barang, qty FROM pemindahan_barang
           WHERE cabang = ? AND tanggal_pindah = ? ORDER BY nama_barang""",
        (cabang, tanggal),
    ).fetchall()


def log_import(
    conn,
    nama_file: str,
    cabang: str | None,
    format_sumber: str | None,
    tanggal_data: str | None,
    n_baru: int,
    n_duplikat: int,
    konteks: str,
):
    import datetime

    conn.execute(
        """INSERT INTO import_log
           (nama_file, cabang, format_sumber, tanggal_data, n_baru, n_duplikat, konteks, waktu_import)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            nama_file,
            cabang,
            format_sumber,
            tanggal_data,
            n_baru,
            n_duplikat,
            konteks,
            datetime.datetime.now().isoformat(timespec="seconds"),
        ),
    )


def list_import_log(conn, limit: int = 300):
    return conn.execute(
        """SELECT waktu_import, nama_file, cabang, format_sumber, tanggal_data,
                  n_baru, n_duplikat, konteks
           FROM import_log ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()


def hapus_semua_pemindahan(conn):
    """Hapus SEMUA record pemindahan_barang (barang_master, cabang_master, dan
    import_log tetap dibiarkan). Dipakai kalau backfill dari Excel ternyata
    mau dibuang total dan diganti sumber lain yang lebih akurat (mis. hasil
    verifikasi manual ke Accurate) -- lihat PRD §11."""
    conn.execute("DELETE FROM pemindahan_barang")

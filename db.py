"""
Skema database & helper query untuk tool cek pemindahan barang.
Lihat PRD §5 untuk desain skema.

Koneksi database -- 3 backend didukung, dipilih otomatis dari environment
variable yang tersedia (urutan prioritas):
  1. Postgres (mis. Neon) kalau DATABASE_URL diset -- BACKEND YANG
     DIREKOMENDASIKAN untuk deploy web (lihat README bagian "Deploy ke Web
     (Tercepat, Gratis)"): dipasangkan dengan hosting app di region Asia
     (Google Cloud Run Jakarta) + database di region Asia (Neon Singapore),
     jauh lebih dekat & cepat dari Indonesia dibanding Turso (Tokyo/Mumbai).
  2. Turso (libsql) kalau TURSO_DATABASE_URL & TURSO_AUTH_TOKEN diset --
     didukung untuk KOMPATIBILITAS ke belakang (deploy lama), tidak lagi
     jadi rekomendasi utama karena region-nya lebih jauh dari Indonesia.
  3. SQLite lokal (fallback) -- dipakai otomatis kalau dua env var di atas
     tidak ada, mis. waktu development di komputer sendiri. JANGAN dipakai
     untuk deploy web (storage-nya tidak persisten di kebanyakan platform
     gratis).

Fungsi-fungsi query di bawah file ini SEMUA ditulis pakai placeholder ala
SQLite ("?", bukan "%s"-nya Postgres) supaya satu source code query bisa
jalan di ketiga backend tanpa perlu ditulis ulang -- untuk Postgres,
`_PGConnAdapter` di bawah ini yang menerjemahkan "?" -> "%s" secara
transparan sebelum query dikirim ke psycopg2.
"""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = "pemindahan.db"  # dipakai kalau tidak ada DATABASE_URL / TURSO_* (mode lokal/dev)


class _PGConnAdapter:
    """Bungkus koneksi psycopg2 (Postgres) supaya punya interface yang sama
    dengan sqlite3.Connection.execute(sql, params) -- yaitu bisa dipanggil
    LANGSUNG di object connection (bukan wajib bikin cursor dulu), dan
    menerima placeholder gaya "?" seperti yang dipakai di semua fungsi query
    pada file ini. Ini satu-satunya lapisan yang perlu tahu bedanya dialek
    SQL Postgres vs SQLite -- fungsi query lain di bawah TIDAK perlu diubah."""

    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, sql: str, params: tuple = ()):
        cur = self._conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def _backend() -> str:
    if os.environ.get("DATABASE_URL"):
        return "postgres"
    if os.environ.get("TURSO_DATABASE_URL") and os.environ.get("TURSO_AUTH_TOKEN"):
        return "turso"
    return "sqlite"


def _id_pk_sql(backend: str) -> str:
    """Sintaks kolom id auto-increment beda antar dialek: Postgres pakai
    SERIAL, SQLite/Turso (libsql, fork dari SQLite) pakai AUTOINCREMENT."""
    return "SERIAL PRIMARY KEY" if backend == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"


def _schema_statements(backend: str) -> list[str]:
    id_pk = _id_pk_sql(backend)
    return [
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
        f"""CREATE TABLE IF NOT EXISTS pemindahan_barang (
            id {id_pk},
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
        f"""CREATE TABLE IF NOT EXISTS import_log (
            id {id_pk},
            nama_file TEXT NOT NULL,
            cabang TEXT,
            format_sumber TEXT,
            tanggal_data TEXT,
            n_baru INTEGER NOT NULL,
            n_duplikat INTEGER NOT NULL,
            konteks TEXT NOT NULL,
            waktu_import TEXT NOT NULL
        )""",
        # Riwayat LENGKAP status jenis barang (PKP / Non-PKP / Keduanya). Setiap
        # perubahan jadi baris baru (bukan ditimpa) -- status SAAT INI = baris
        # dengan waktu_ubah terbaru untuk kombinasi format_sumber+sku itu.
        f"""CREATE TABLE IF NOT EXISTS jenis_barang_riwayat (
            id {id_pk},
            format_sumber TEXT NOT NULL,
            sku TEXT NOT NULL,
            jenis TEXT NOT NULL,
            sumber TEXT NOT NULL,
            waktu_ubah TEXT NOT NULL
        )""",
        """CREATE INDEX IF NOT EXISTS idx_jenis_barang_lookup
            ON jenis_barang_riwayat (format_sumber, sku, waktu_ubah)""",
        # Histori setiap kali barang dicek (lewat upload file ATAU cek manual).
        # UNIQUE per (cabang, sku, tanggal): cek ulang untuk kombinasi yang sama
        # akan MEMPERBARUI baris yang sudah ada, bukan menumpuk duplikat.
        f"""CREATE TABLE IF NOT EXISTS penjualan_barang (
            id {id_pk},
            format_sumber TEXT NOT NULL,
            sku TEXT NOT NULL,
            nama_barang TEXT NOT NULL,
            cabang TEXT NOT NULL,
            tanggal TEXT NOT NULL,
            qty_terjual REAL NOT NULL,
            sumber TEXT NOT NULL,
            waktu_cek TEXT NOT NULL
        )""",
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_penjualan_dedup
            ON penjualan_barang (cabang, sku, tanggal)""",
        """CREATE INDEX IF NOT EXISTS idx_penjualan_cari
            ON penjualan_barang (tanggal, nama_barang, cabang, sku)""",
    ]


@contextmanager
def get_conn(db_path: str = DB_PATH):
    """Buka koneksi ke Postgres (kalau DATABASE_URL diset), Turso (kalau
    TURSO_DATABASE_URL/TOKEN diset), atau file SQLite lokal (fallback
    dev/testing). Dipanggil sebagai context manager: `with get_conn() as conn:`."""
    backend = _backend()
    if backend == "postgres":
        import psycopg2  # hanya di-import kalau memang dipakai

        conn = _PGConnAdapter(psycopg2.connect(os.environ["DATABASE_URL"]))
    elif backend == "turso":
        import libsql  # hanya di-import kalau memang dipakai

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


def _kolom_tabel(conn, backend: str, tabel: str) -> list[str]:
    """Daftar nama kolom sebuah tabel -- dipakai untuk deteksi skema lama
    sebelum migrasi. PRAGMA table_info cuma ada di SQLite/Turso; Postgres
    pakai information_schema.columns (hasilnya sama-sama list nama kolom)."""
    if backend == "postgres":
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (tabel,),
        ).fetchall()
    else:
        rows = [(None, row[1]) for row in conn.execute(f"PRAGMA table_info({tabel})").fetchall()]
    return [r[0] if backend == "postgres" else r[1] for r in rows]


def _migrasi_penjualan_barang_lama(conn, backend: str):
    """Migrasi satu-kali: versi lama tabel penjualan_barang punya kolom
    valid/alasan/jenis_barang yang wajib diisi (NOT NULL). Kolom itu sudah
    dibuang dari skema baru, tapi CREATE TABLE IF NOT EXISTS tidak mengubah
    tabel yang sudah kepalang dibuat -- jadi INSERT versi baru (tanpa kolom
    itu) gagal kena constraint lama. Migrasi ini deteksi skema lama, lalu
    bongkar-pasang ulang tabelnya, memindahkan data yang masih kompatibel."""
    cols = _kolom_tabel(conn, backend, "penjualan_barang")
    if "valid" not in cols:
        return  # sudah skema baru, tidak perlu migrasi
    id_pk = _id_pk_sql(backend)
    conn.execute("ALTER TABLE penjualan_barang RENAME TO penjualan_barang_lama")
    conn.execute(
        f"""CREATE TABLE penjualan_barang (
            id {id_pk},
            format_sumber TEXT NOT NULL,
            sku TEXT NOT NULL,
            nama_barang TEXT NOT NULL,
            cabang TEXT NOT NULL,
            tanggal TEXT NOT NULL,
            qty_terjual REAL NOT NULL,
            sumber TEXT NOT NULL,
            waktu_cek TEXT NOT NULL
        )"""
    )
    conn.execute(
        """INSERT INTO penjualan_barang
           (format_sumber, sku, nama_barang, cabang, tanggal, qty_terjual, sumber, waktu_cek)
           SELECT format_sumber, sku, nama_barang, cabang, tanggal, qty_terjual, sumber, waktu_cek
           FROM penjualan_barang_lama"""
    )
    conn.execute("DROP TABLE penjualan_barang_lama")
    # Index lama ikut "menempel" ke tabel lama saat di-RENAME, jadi ikut hilang
    # waktu tabel lama di-DROP -- perlu dibuat ulang di sini untuk tabel baru.
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_penjualan_dedup
           ON penjualan_barang (cabang, sku, tanggal)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_penjualan_cari
           ON penjualan_barang (tanggal, nama_barang, cabang, sku)"""
    )


def _migrasi_tambah_jenis_ke_penjualan(conn, backend: str):
    """Migrasi satu-kali: tambah kolom jenis_barang ke penjualan_barang (skema
    sempat membuang kolom ini, sekarang ditambah lagi -- kali ini nullable,
    supaya baris LAMA yang sudah tercatat tanpa info jenis tetap valid, cuma
    tampil 'Belum diklasifikasikan' untuk baris lama itu)."""
    cols = _kolom_tabel(conn, backend, "penjualan_barang")
    if "jenis_barang" not in cols:
        conn.execute("ALTER TABLE penjualan_barang ADD COLUMN jenis_barang TEXT")


def init_db(db_path: str = DB_PATH):
    backend = _backend()
    with get_conn(db_path) as conn:
        for stmt in _schema_statements(backend):
            conn.execute(stmt)
        _migrasi_penjualan_barang_lama(conn, backend)
        _migrasi_tambah_jenis_ke_penjualan(conn, backend)


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
    -- lihat uq_pemindahan_dedup di SCHEMA. Pakai INSERT...RETURNING (1 round-trip)
    daripada INSERT lalu SELECT terpisah (2 round-trip) -- penting buat performa
    karena tiap query ke Turso itu 1 kali lewat jaringan."""
    cur = conn.execute(
        """INSERT INTO pemindahan_barang
           (format_sumber, sku, nama_barang, cabang, tanggal_pindah, qty, sumber, file_asal)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(cabang, sku, tanggal_pindah, qty) DO NOTHING
           RETURNING id""",
        (format_sumber, sku, nama_barang, cabang, tanggal_pindah, qty, sumber, file_asal),
    )
    row = cur.fetchone()
    return row[0] if row else None


def simpan_pemindahan_dari_parsed(conn, parsed, sumber: str, file_asal: str | None = None) -> list[int]:
    """Helper terpusat: dari hasil parser.parse_file(), simpan tiap baris dengan
    qty_masuk > 0 sebagai record pemindahan (dedup otomatis lewat insert_pemindahan).
    Dipakai oleh backfill.py, tab Backfill, DAN tab Pengecekan Harian.

    PENTING soal performa: cuma baris qty_masuk > 0 yang di-upsert ke barang_master
    di sini (bukan SEMUA ~900 baris per file) -- karena tiap query ke Turso adalah
    1 round-trip jaringan, upsert 900 baris/file itu yang bikin upload lambat
    (bisa 10 menit/file). Konsekuensinya: barang yang TIDAK PERNAH punya Receive/
    Masuk > 0 di file manapun tidak otomatis masuk katalog. Kalau butuh katalog
    lengkap (semua barang, termasuk yang belum pernah masuk), pakai
    sinkronkan_katalog_lengkap() secara terpisah (jalan sesekali, bukan tiap upload).

    Return: daftar id record BARU yang benar-benar tersimpan (bukan duplikat)."""
    upsert_cabang(conn, parsed.cabang, parsed.format_sumber)
    id_baru = []
    for row in parsed.rows:
        if row.qty_masuk > 0:
            upsert_barang(conn, parsed.format_sumber, row.sku, row.nama_barang, row.category)
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


def sinkronkan_katalog_lengkap(conn, parsed) -> int:
    """Upsert SEMUA baris (bukan cuma qty_masuk > 0) ke barang_master --
    dipakai lewat tombol terpisah 'Sinkronkan Katalog Lengkap', dijalankan
    sesekali kalau perlu katalog barang yang benar-benar lengkap (termasuk
    barang yang belum pernah ada transaksi Receive-nya). TIDAK dipanggil
    otomatis tiap upload harian karena lambat (lihat catatan performa di
    simpan_pemindahan_dari_parsed)."""
    upsert_cabang(conn, parsed.cabang, parsed.format_sumber)
    n = 0
    for row in parsed.rows:
        upsert_barang(conn, parsed.format_sumber, row.sku, row.nama_barang, row.category)
        n += 1
    return n


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


# ---------------------------------------------------------------------------
# Jenis Barang: PKP / Non-PKP / Keduanya -- riwayat lengkap (bukan cuma status
# terkini yang ditimpa).
# ---------------------------------------------------------------------------
JENIS_VALID = ("pkp", "nonpkp", "keduanya")


def set_jenis_barang(conn, format_sumber: str, sku: str, jenis: str, sumber: str):
    """Catat perubahan jenis barang sebagai baris RIWAYAT baru (bukan update di
    tempat) -- supaya histori lengkap kapan status berubah dari apa ke apa."""
    import datetime

    if jenis not in JENIS_VALID:
        raise ValueError(f"jenis harus salah satu dari {JENIS_VALID}, dapat: {jenis!r}")
    conn.execute(
        """INSERT INTO jenis_barang_riwayat (format_sumber, sku, jenis, sumber, waktu_ubah)
           VALUES (?, ?, ?, ?, ?)""",
        (format_sumber, sku, jenis, sumber, datetime.datetime.now().isoformat(timespec="seconds")),
    )


def get_jenis_barang(conn, format_sumber: str, sku: str) -> str | None:
    """Status jenis TERKINI (baris riwayat terbaru) untuk 1 barang. None kalau
    belum pernah diklasifikasikan sama sekali."""
    row = conn.execute(
        """SELECT jenis FROM jenis_barang_riwayat
           WHERE format_sumber = ? AND sku = ?
           ORDER BY waktu_ubah DESC, id DESC LIMIT 1""",
        (format_sumber, sku),
    ).fetchone()
    return row[0] if row else None


def list_riwayat_jenis_barang(conn, format_sumber: str, sku: str):
    """Seluruh riwayat perubahan jenis untuk 1 barang, terbaru dulu."""
    return conn.execute(
        """SELECT jenis, sumber, waktu_ubah FROM jenis_barang_riwayat
           WHERE format_sumber = ? AND sku = ?
           ORDER BY waktu_ubah DESC, id DESC""",
        (format_sumber, sku),
    ).fetchall()


def list_barang_dengan_jenis(
    conn,
    format_sumber: str | None = None,
    filter_jenis: str | None = None,  # 'pkp' | 'nonpkp' | 'keduanya' | 'belum'
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """Daftar barang di barang_master + status jenis TERKINI-nya (LEFT JOIN ke
    baris riwayat terbaru per barang). filter_jenis='belum' artinya barang yang
    sama sekali belum pernah diklasifikasikan."""
    where = []
    params: list = []
    if format_sumber:
        where.append("bm.format_sumber = ?")
        params.append(format_sumber)
    if search:
        where.append("(bm.nama_barang LIKE ? OR bm.sku LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    if filter_jenis == "belum":
        where.append("terkini.jenis IS NULL")
    elif filter_jenis in JENIS_VALID:
        where.append("terkini.jenis = ?")
        params.append(filter_jenis)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.extend([limit, offset])
    sql = f"""
        SELECT bm.format_sumber, bm.sku, bm.nama_barang, terkini.jenis
        FROM barang_master bm
        LEFT JOIN (
            SELECT format_sumber, sku, jenis
            FROM jenis_barang_riwayat jr
            WHERE waktu_ubah = (
                SELECT MAX(waktu_ubah) FROM jenis_barang_riwayat jr2
                WHERE jr2.format_sumber = jr.format_sumber AND jr2.sku = jr.sku
            )
        ) terkini ON terkini.format_sumber = bm.format_sumber AND terkini.sku = bm.sku
        {where_sql}
        ORDER BY bm.nama_barang LIMIT ? OFFSET ?
    """
    return conn.execute(sql, tuple(params)).fetchall()


def count_barang_dengan_jenis(
    conn,
    format_sumber: str | None = None,
    filter_jenis: str | None = None,
    search: str | None = None,
) -> int:
    where = []
    params: list = []
    if format_sumber:
        where.append("bm.format_sumber = ?")
        params.append(format_sumber)
    if search:
        where.append("(bm.nama_barang LIKE ? OR bm.sku LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    if filter_jenis == "belum":
        where.append("terkini.jenis IS NULL")
    elif filter_jenis in JENIS_VALID:
        where.append("terkini.jenis = ?")
        params.append(filter_jenis)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT COUNT(*)
        FROM barang_master bm
        LEFT JOIN (
            SELECT format_sumber, sku, jenis
            FROM jenis_barang_riwayat jr
            WHERE waktu_ubah = (
                SELECT MAX(waktu_ubah) FROM jenis_barang_riwayat jr2
                WHERE jr2.format_sumber = jr.format_sumber AND jr2.sku = jr.sku
            )
        ) terkini ON terkini.format_sumber = bm.format_sumber AND terkini.sku = bm.sku
        {where_sql}
    """
    row = conn.execute(sql, tuple(params)).fetchone()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Validitas dengan detail alasan + status jenis barang (PKP/Non-PKP/Keduanya).
# Aturan (dikonfirmasi client):
#   - Non-PKP  -> SELALU tidak valid, apapun histori pemindahannya.
#   - PKP / Keduanya / belum diklasifikasikan -> pakai aturan lama (pernah ada
#     Receive/Masuk sejak tanggal_minimal ke cabang yang sama).
# ---------------------------------------------------------------------------
LABEL_JENIS = {
    "pkp": "PKP",
    "nonpkp": "Non-PKP",
    "keduanya": "Keduanya",
    None: "Belum diklasifikasikan",
}


def cek_validitas_detail(
    conn, format_sumber: str, cabang: str, sku: str, tanggal_minimal: str = "2026-08-01"
) -> tuple[bool, str, str]:
    """Return (valid, alasan, label_jenis)."""
    jenis = get_jenis_barang(conn, format_sumber, sku)
    label = LABEL_JENIS.get(jenis, jenis)

    if jenis == "nonpkp":
        return False, "Barang Non-PKP -- tidak diinput terlepas dari histori pemindahan.", label

    row = conn.execute(
        """SELECT tanggal_pindah FROM pemindahan_barang
           WHERE cabang = ? AND sku = ? AND tanggal_pindah >= ?
           ORDER BY tanggal_pindah ASC LIMIT 1""",
        (cabang, sku, tanggal_minimal),
    ).fetchone()
    if row:
        return True, f"Ada catatan pemindahan barang ke cabang ini pada {row[0]}.", label
    return False, "Barang tidak memiliki catatan pemindahan ke cabang ini sejak Agustus.", label


# ---------------------------------------------------------------------------
# Histori penjualan (hasil tiap pengecekan, dari upload file ATAU cek manual).
# ---------------------------------------------------------------------------
def simpan_penjualan(
    conn,
    format_sumber: str,
    sku: str,
    nama_barang: str,
    cabang: str,
    tanggal: str,
    qty_terjual: float,
    sumber: str,
    jenis_barang: str | None = None,
):
    """Upsert 1 baris histori penjualan. Catatan apa yang terjual + label
    JENIS BARANG SAAT ITU DICEK (snapshot terkunci -- lihat catatan di bawah).

    Cek ulang untuk kombinasi cabang+sku+tanggal yang sama akan MEMPERBARUI
    qty (bukan menumpuk duplikat). TAPI kolom jenis_barang SENGAJA TIDAK ikut
    diperbarui saat konflik -- begitu sebuah baris pertama kali tercatat,
    jenisnya terkunci selamanya sesuai kondisi saat itu, walau barangnya
    diklasifikasikan ulang belakangan (mis. dari Non-PKP jadi PKP). Ini
    keputusan desain yang disengaja, dikonfirmasi ke user."""
    import datetime

    conn.execute(
        """INSERT INTO penjualan_barang
           (format_sumber, sku, nama_barang, cabang, tanggal, qty_terjual, sumber, waktu_cek, jenis_barang)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(cabang, sku, tanggal) DO UPDATE SET
               qty_terjual = excluded.qty_terjual,
               sumber = excluded.sumber,
               waktu_cek = excluded.waktu_cek""",
        (
            format_sumber, sku, nama_barang, cabang, tanggal, qty_terjual, sumber,
            datetime.datetime.now().isoformat(timespec="seconds"), jenis_barang,
        ),
    )


def search_penjualan(
    conn,
    cabang: str | None = None,
    sku: str | None = None,
    nama: str | None = None,
    tanggal_dari: str | None = None,
    tanggal_sampai: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    where = []
    params: list = []
    if cabang:
        where.append("cabang = ?")
        params.append(cabang)
    if sku:
        where.append("sku LIKE ?")
        params.append(f"%{sku}%")
    if nama:
        where.append("nama_barang LIKE ?")
        params.append(f"%{nama}%")
    if tanggal_dari:
        where.append("tanggal >= ?")
        params.append(tanggal_dari)
    if tanggal_sampai:
        where.append("tanggal <= ?")
        params.append(tanggal_sampai)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.extend([limit, offset])
    return conn.execute(
        f"""SELECT id, tanggal, cabang, sku, nama_barang, qty_terjual, sumber, jenis_barang
            FROM penjualan_barang {where_sql}
            ORDER BY tanggal DESC, id DESC LIMIT ? OFFSET ?""",
        tuple(params),
    ).fetchall()


def update_penjualan(conn, id_: int, tanggal: str, qty_terjual: float):
    conn.execute(
        "UPDATE penjualan_barang SET tanggal = ?, qty_terjual = ? WHERE id = ?",
        (tanggal, qty_terjual, id_),
    )


def delete_penjualan(conn, id_: int):
    conn.execute("DELETE FROM penjualan_barang WHERE id = ?", (id_,))


def count_penjualan(
    conn,
    cabang: str | None = None,
    sku: str | None = None,
    nama: str | None = None,
    tanggal_dari: str | None = None,
    tanggal_sampai: str | None = None,
) -> int:
    where = []
    params: list = []
    if cabang:
        where.append("cabang = ?")
        params.append(cabang)
    if sku:
        where.append("sku LIKE ?")
        params.append(f"%{sku}%")
    if nama:
        where.append("nama_barang LIKE ?")
        params.append(f"%{nama}%")
    if tanggal_dari:
        where.append("tanggal >= ?")
        params.append(tanggal_dari)
    if tanggal_sampai:
        where.append("tanggal <= ?")
        params.append(tanggal_sampai)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    row = conn.execute(
        f"SELECT COUNT(*) FROM penjualan_barang {where_sql}", tuple(params)
    ).fetchone()
    return row[0] if row else 0

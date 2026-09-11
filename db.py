import os
import sqlite3
from contextlib import contextmanager

DB_PATH = "pemindahan.db"

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
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_pemindahan_dedup
        ON pemindahan_barang (cabang, sku, tanggal_pindah, qty)""",
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
    """CREATE TABLE IF NOT EXISTS jenis_barang_riwayat (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        format_sumber TEXT NOT NULL,
        sku TEXT NOT NULL,
        jenis TEXT NOT NULL,
        sumber TEXT NOT NULL,
        waktu_ubah TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_jenis_barang_lookup
        ON jenis_barang_riwayat (format_sumber, sku, waktu_ubah)""",
    """CREATE TABLE IF NOT EXISTS penjualan_barang (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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


def _is_turso_configured() -> bool:
    return bool(os.environ.get("TURSO_DATABASE_URL")) and bool(os.environ.get("TURSO_AUTH_TOKEN"))


@contextmanager
def get_conn(db_path: str = DB_PATH):
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


def _migrasi_penjualan_barang_lama(conn):
    cols = [row[1] for row in conn.execute("PRAGMA table_info(penjualan_barang)").fetchall()]
    if "valid" not in cols:
        return  
    conn.execute("ALTER TABLE penjualan_barang RENAME TO penjualan_barang_lama")
    conn.execute(
        """CREATE TABLE penjualan_barang (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_penjualan_dedup
           ON penjualan_barang (cabang, sku, tanggal)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_penjualan_cari
           ON penjualan_barang (tanggal, nama_barang, cabang, sku)"""
    )


def _migrasi_tambah_jenis_ke_penjualan(conn):
    cols = [row[1] for row in conn.execute("PRAGMA table_info(penjualan_barang)").fetchall()]
    if "jenis_barang" not in cols:
        conn.execute("ALTER TABLE penjualan_barang ADD COLUMN jenis_barang TEXT")


def init_db(db_path: str = DB_PATH):
    with get_conn(db_path) as conn:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)
        _migrasi_penjualan_barang_lama(conn)
        _migrasi_tambah_jenis_ke_penjualan(conn)


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
    upsert_cabang(conn, parsed.cabang, parsed.format_sumber)
    n = 0
    for row in parsed.rows:
        upsert_barang(conn, parsed.format_sumber, row.sku, row.nama_barang, row.category)
        n += 1
    return n


def cek_validitas(conn, cabang: str, sku: str, tanggal_minimal: str = "2026-08-01") -> bool:
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
    tanggal: str | None = None,
    limit: int = 200,
    offset: int = 0,
):
    where = []
    params = []
    if cabang:
        where.append("cabang = ?")
        params.append(cabang)
    if tanggal:
        where.append("tanggal_pindah = ?")
        params.append(tanggal)
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


def count_pemindahan(
    conn, cabang: str | None = None, search: str | None = None, tanggal: str | None = None
) -> int:
    where = []
    params = []
    if cabang:
        where.append("cabang = ?")
        params.append(cabang)
    if tanggal:
        where.append("tanggal_pindah = ?")
        params.append(tanggal)
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
    return conn.execute(
        """SELECT tanggal_pindah, COUNT(*) AS jumlah_barang, SUM(qty) AS total_qty
           FROM pemindahan_barang WHERE cabang = ?
           GROUP BY tanggal_pindah ORDER BY tanggal_pindah DESC""",
        (cabang,),
    ).fetchall()


def list_detail_tanggal(conn, cabang: str, tanggal: str):
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
    conn.execute("DELETE FROM pemindahan_barang")

JENIS_VALID = ("pkp", "nonpkp", "keduanya")

def set_jenis_barang(conn, format_sumber: str, sku: str, jenis: str, sumber: str):
    import datetime

    if jenis not in JENIS_VALID:
        raise ValueError(f"jenis harus salah satu dari {JENIS_VALID}, dapat: {jenis!r}")
    conn.execute(
        """INSERT INTO jenis_barang_riwayat (format_sumber, sku, jenis, sumber, waktu_ubah)
           VALUES (?, ?, ?, ?, ?)""",
        (format_sumber, sku, jenis, sumber, datetime.datetime.now().isoformat(timespec="seconds")),
    )


def get_jenis_barang(conn, format_sumber: str, sku: str) -> str | None:
    row = conn.execute(
        """SELECT jenis FROM jenis_barang_riwayat
           WHERE format_sumber = ? AND sku = ?
           ORDER BY waktu_ubah DESC, id DESC LIMIT 1""",
        (format_sumber, sku),
    ).fetchone()
    return row[0] if row else None


def list_riwayat_jenis_barang(conn, format_sumber: str, sku: str):
    return conn.execute(
        """SELECT jenis, sumber, waktu_ubah FROM jenis_barang_riwayat
           WHERE format_sumber = ? AND sku = ?
           ORDER BY waktu_ubah DESC, id DESC""",
        (format_sumber, sku),
    ).fetchall()


def list_barang_dengan_jenis(
    conn,
    format_sumber: str | None = None,
    filter_jenis: str | None = None,  
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
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
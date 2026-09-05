# Tool Cek Validitas Barang — Penjualan Cabang

Lihat `PRD_Tool_Cek_Pemindahan_Barang.md` untuk latar belakang & desain lengkap.

## Login

Satu user terdaftar: **username `admin`, password `ujangadmin`**. Bisa
dioverride lewat environment variable `APP_USERNAME` / `APP_PASSWORD` kalau
mau diganti tanpa ubah kode.

Catatan jujur: form login sudah diset `autocomplete="off"`/`"new-password"`
sebagai permintaan ke browser supaya tidak menawarkan simpan password. Ini
**tidak dijamin selalu berhasil** — browser modern (terutama Chrome) kadang
tetap menampilkan prompt simpan password karena itu keputusan browser sendiri,
bukan sesuatu yang bisa dipaksa penuh dari sisi aplikasi.

---

## Pemakaian Lokal (development/testing)

```
pip install -r requirements.txt
streamlit run app.py
```

Tanpa konfigurasi tambahan, tool otomatis pakai file SQLite lokal
(`pemindahan.db`) — cukup untuk development/testing di komputer sendiri.
**Jangan pakai mode ini untuk deploy ke web** (lihat bagian "Kenapa Turso"
di bawah).

### Backfill awal (sekali di awal, atau tiap dapat file lama baru)

```
python backfill.py /path/ke/folder/Agustus
```

Atau lewat tab "Backfill (Import Massal)" di aplikasi web.

---

## Deploy ke Web (Gratis) — Streamlit Community Cloud + Turso

### Kenapa butuh 2 layanan (bukan cuma 1)?

Streamlit Community Cloud gratis untuk **menjalankan aplikasinya**, tapi
storage lokalnya **tidak persisten** — dari dokumentasi resmi Streamlit:
*"Community Cloud apps do not guarantee the persistence of local file
storage, so the platform may delete data stored using this technique at any
time."* Kalau database SQLite kita taruh sebagai file lokal di situ, **histori
pemindahan bisa hilang tiba-tiba** kapan saja app-nya restart/redeploy — ini
bukan risiko kecil, karena seluruh nilai tool ini ada di data historisnya.

Solusinya: pisahkan **penyimpanan data** ke **Turso** — database cloud
gratis (SQLite-compatible, jadi hampir tidak perlu ubah kode) yang persisten
selamanya, tidak terikat ke siklus hidup aplikasi.

**Konsekuensi yang perlu diterima:** app di Streamlit Community Cloud akan
"tidur" kalau tidak ada yang mengakses selama 12 jam — begitu dibuka lagi,
perlu klik "bangunkan" dan tunggu ~30 detik. **Datanya tetap aman** (tersimpan
di Turso, bukan di app), cuma app-nya perlu di-restart manual. Ini trade-off
yang sudah disepakati demi tetap 100% gratis.

### Langkah 1 — Setup Turso

1. Daftar di [turso.tech](https://turso.tech) (gratis, tidak perlu kartu kredit).
2. Install Turso CLI, lalu buat database:
   ```
   turso db create pemindahan-barang
   ```
3. Ambil URL koneksi & token:
   ```
   turso db show pemindahan-barang --url
   turso db tokens create pemindahan-barang
   ```
4. Catat dua nilai ini (`TURSO_DATABASE_URL` dan `TURSO_AUTH_TOKEN`) — dipakai di Langkah 3.

### Langkah 2 — Push kode ke GitHub

1. Buat repo baru (boleh private) di GitHub.
2. Push seluruh isi folder `app/` ke repo itu (`app.py`, `db.py`, `parser.py`,
   `backfill.py`, `requirements.txt`).
3. **Jangan** commit file `pemindahan.db` lokal kalau ada (tambahkan ke
   `.gitignore`) — begitu Turso aktif, itu tidak dipakai lagi.

### Langkah 3 — Deploy di Streamlit Community Cloud

1. Buka [share.streamlit.io](https://share.streamlit.io), login pakai akun GitHub.
2. "New app" → pilih repo yang baru dibuat, branch, dan `app.py` sebagai main file.
3. Sebelum deploy, buka "Advanced settings" → "Secrets", isi:
   ```toml
   TURSO_DATABASE_URL = "libsql://nama-db-kamu.turso.io"
   TURSO_AUTH_TOKEN = "isi-token-dari-langkah-1"
   ```
   (Boleh sekalian tambahkan `APP_USERNAME` / `APP_PASSWORD` di sini kalau
   mau override dari default `admin` / `ujangadmin`.)
4. Klik "Deploy". Tunggu beberapa menit, app akan dapat URL publik
   (`https://nama-app-kamu.streamlit.app`).
5. Begitu app hidup, jalankan backfill Agustus pertama kali lewat tab
   "Backfill" di app (upload file, bukan lewat CLI, karena CLI jalan di
   komputer lokal sedangkan database sekarang ada di Turso — cukup pastikan
   `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` juga diset sebagai environment
   variable di komputer lokal kalau mau pakai `backfill.py` dari CLI
   terhubung ke Turso yang sama).

### Kalau app "tidur"

Siapa saja yang buka linknya akan lihat halaman "app ini sedang tidur,
klik untuk membangunkan" — klik itu, tunggu ~30 detik, app hidup lagi.
Data tidak hilang karena tersimpan di Turso, bukan di app.

---

## Cara Pakai Fitur

- **Tab "Pengecekan Harian"**:
  - Upload 1 file Excel penjualan cabang hari ini → tool tampilkan barang yang
    terjual, qty asli kalau valid, qty 0 kalau tidak valid.
  - Checkbox "Simpan juga ke histori pemindahan" (default aktif): file yang
    sama sekaligus menambah histori (dedup otomatis). **Kalau dicentang lalu
    dimatikan lagi, record yang SUDAH sempat tersimpan dari file itu otomatis
    DIHAPUS lagi** (bukan cuma berhenti menambah yang baru) — pelacakannya per
    file yang sedang diupload di sesi itu, aman tidak menyentuh data dari
    sumber lain.
  - **Baru — Cek cepat 1 barang tanpa file**: pilih cabang & barang langsung
    dari dropdown, isi qty, klik "Cek Validitas" — untuk pengecekan spontan
    tanpa perlu punya file excel-nya.
- **Tab "Kelola Data Pemindahan"**:
  - Tambah record manual (dropdown barang + cabang + tanggal + qty).
  - **Klasifikasi per tanggal**: pilih cabang, lihat tabel ringkas semua
    tanggal yang punya record pemindahan. Pilih salah satu tanggal lalu klik
    tombol **"Lihat Detail"** untuk menampilkan rincian barang & qty di
    tanggal itu (tidak langsung ditampilkan semua, biar tidak berat).
  - **Data pemindahan tersimpan**: sekarang ada **kotak pencarian** (nama
    barang/SKU) dan **pagination** (50 baris per halaman, tombol Sebelumnya/
    Berikutnya) — jadi data ribuan baris tetap bisa ditelusuri semua, tidak
    cuma 200 baris pertama seperti sebelumnya.
  - Edit/hapus record yang sudah ada (buat koreksi kalau ada revisi nota).
  - **Zona berbahaya**: hapus SEMUA data pemindahan sekaligus (perlu ketik
    konfirmasi) — dipakai kalau mau ganti total sumber data.
- **Tab "Backfill"**: import banyak file Excel sekaligus lewat browser.
- **Tab "Riwayat Import"**: log semua file yang pernah diproses tool.
- **Login**: username `admin`, password `ujangadmin` (bisa dioverride lewat
  env var `APP_USERNAME`/`APP_PASSWORD`). Tampilan dikembalikan minimalis
  (form polos, tanpa header berwarna/ikon) sesuai preferensi.

## Demo cepat tanpa data operasional riil

Folder `dummy_harian/` berisi 2 file contoh (tanggal 01/09/2026, di luar
cakupan Agustus) untuk langsung dites di tab "Pengecekan Harian":

- `Laporan_Stok_-_Movement_Stock_L-2026-09-01__LS_LOSARI_.xlsx` — 5 barang
  valid + 3 tidak valid. Hasil yang benar: 5 baris qty asli, 3 baris qty 0.
- `daftar-stok_2026-09-01__LS_GUMUL_.xlsx` — pola sama, format Daftar Stok.

## Catatan penting: nama cabang bisa beda antara nama file dan isi file

File bernama "SJ Puri" isinya (`Cabang:` di dalam Excel) selalu
`SARI JAYA 23 WIJAYA KUSUMA`; file "SJ Mayjen" selalu `SARI JAYA 22
MAGERSARI`. Ini alias/nama panggilan, bukan salah ketik. **Tool selalu
memakai nama cabang dari ISI file** (bukan nama file), jadi ini otomatis
ditangani benar.

## Catatan implementasi

- Mendukung 2 format laporan otomatis: `Laporan Stok - Movement Stock` dan
  `Daftar Stok` — dideteksi dari isi file, bukan nama file.
- Kolom SKU pada format Movement Stock ditulis sebagai formula (`="A01"`),
  ditangani otomatis oleh parser.
- Baris tanpa SKU (mis. baris kategori "SLOKI") dan baris "TOTAL" diabaikan.
- File dengan periode rentang tanggal dicatat memakai tanggal AWAL rentang.
- Dedup pemindahan: unique index di `(cabang, sku, tanggal_pindah, qty)`.
- Ambang tanggal valid (`TANGGAL_MINIMAL = "2026-08-01"`) diatur langsung di
  `app.py`.

## Known limitation (jujur, belum diperbaiki, perlu keputusan)

Validitas cuma cek **"pernah ada Receive sejak 1 Agustus"** — tidak mengecek
apakah Receive itu terjadi **sebelum** tanggal penjualan yang sedang dicek.
Aman untuk pemakaian harian real-time; berisiko kecil kalau dipakai untuk
cek ulang tanggal lama. Lihat PRD §3.3 untuk contoh kasus nyata.

## Known limitation lain: Excel adalah snapshot, bisa ketinggalan dari Accurate

Diverifikasi ke transaksi resmi Accurate (4 transfer, 3 cabang, tanggal
31/8): 1 cabang cocok 100%, tapi 2 cabang lain **sama sekali tidak muncul**
di Excel meski transfer resminya ada di Accurate — kemungkinan besar karena
transfer itu baru diinput belakangan (backdated), setelah Excel-nya
di-export. Ini bukan bug tool: solusinya adalah **selalu export ulang &
backfill ulang Excel setelah ada input susulan/backdated** di Accurate,
bukan sesuatu yang bisa dideteksi otomatis oleh tool. Lihat PRD §3.3 untuk
detail lengkap kasus ini.

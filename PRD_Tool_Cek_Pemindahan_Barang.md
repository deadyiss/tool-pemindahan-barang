# PRD — Tool Cek Validitas Barang untuk Input Penjualan Cabang

## 1. Latar Belakang

Client mengelola input data penjualan ke sistem kerja (Accurate 5), per cabang, setiap hari. Sistem kerja punya aturan: barang cuma bisa diinput sebagai "penjualan cabang" **jika** barang itu pernah dipindahkan dari gudang pusat ke cabang tersebut (via fitur "Pemindahan Barang" di Accurate), dan pemindahannya tercatat **sejak 1 Agustus 2026** — data sebelum itu tidak valid lagi karena sistem sempat migrasi berkali-kali.

Tiap hari client menerima 1 file Excel per cabang berisi seluruh pergerakan stok cabang itu (bukan cuma yang laku — mayoritas barang qty-nya 0 hari itu). Untuk tahu barang mana yang boleh diinput, client harus mencari manual barang yang laku, lalu mengecek satu-satu apakah barang itu pernah dipindah ke cabang tersebut. Dikerjakan berulang untuk puluhan cabang setiap hari — lambat dan rawan meleset.

## 2. Solusi

Tool berbasis web yang otomatis melakukan pengecekan itu:
1. Client upload file Excel penjualan cabang hari itu.
2. Tool ambil barang yang laku, cek ke database histori pemindahan.
3. Tool tampilkan hasil — qty asli kalau valid, qty 0 kalau tidak valid — client tinggal baca, tidak perlu cek manual.

Client juga bisa menambah/edit/hapus data pemindahan barang secara manual (untuk koreksi/revisi nota), melihat klasifikasi barang masuk per tanggal per cabang, melihat riwayat semua file yang pernah diproses tool, dan mengganti total sumber data kalau suatu saat ada sumber yang lebih akurat.

## 3. Sumber Data

**Kolom `Receive` (format Movement Stock) / `Masuk` (format Daftar Stok) di file Excel adalah satu-satunya sumber data pemindahan barang yang dipakai tool.** Keduanya representasi hal yang sama (dikonfirmasi client), cuma beda nama kolom mengikuti format laporannya. Bukan notes manual client, dan bukan export langsung dari Accurate.

### 3.1 Kenapa bukan notes

Client mencatat notes manual (checklist di aplikasi Notes HP) sebagai alat bantu pribadi — fungsinya penanda tanggal dan penanda barang apa saja yang masuk ke tiap cabang, supaya client tidak perlu membuka semua file Excel satu-satu saat mencari manual. Detail pastinya (qty, dsb.) tetap dicek balik ke Excel/Accurate. Jadi notes adalah **hasil kerja manual client mencocokkan penjualan ke histori pemindahan** — persis logika yang sama dengan yang dikerjakan tool ini, cuma versi manual.

Karena manual, prosesnya terbukti tidak selalu presisi. Cross-check ke Excel asli (dilakukan di 2 cabang, 8 tanggal berbeda, total puluhan item):
- Sebagian besar tanggal cocok sempurna (nama, qty, dan tanggal identik ke Excel).
- Beberapa kali qty tercatat salah (mis. notes tulis 12, Excel/sistem tulis 20 untuk barang & tanggal yang sama).
- Beberapa kali tanggal meleset beberapa hari dari tanggal sebenarnya di Excel.
- Beberapa item di notes tidak ditemukan sama sekali di Excel manapun sepanjang bulan.
- Notes juga tidak pernah mencatat SEMUA barang yang masuk — cuma sebagian dibanding total baris Receive>0 di Excel pada tanggal yang sama.

**Kesimpulan: notes tidak reliable dijadikan acuan otomatis.** Begitu tool membaca histori langsung dari Excel, fungsi notes sebagai "alat bantu cari manual" sudah tergantikan.

### 3.2 Kenapa bukan export langsung dari Accurate

Accurate punya modul "Pemindahan Barang" (Item Transfer) yang terstruktur — transaksi resmi dengan No. Transfer, Tanggal, Dari (PUSAT), Tujuan cabang, dan rincian barang. Ini secara prinsip sumber paling akurat. **Client sudah konfirmasi data ini tidak bisa di-export.** Client sempat berencana screenshot manual satu-satu transaksi Pemindahan Barang dari Accurate untuk dijadikan acuan, tapi volumenya terlalu banyak untuk dikerjakan sekarang. Excel harian tetap dipakai sebagai sumber utama untuk saat ini.

### 3.3 Keterbatasan yang disadari dari pemakaian Excel sebagai acuan

Ada kasus terverifikasi (`Anggur Rose Pink Atlas`, cabang Wijaya Kusuma) di mana laporan Excel tanggal 2 Agustus menunjukkan aktivitas stok, padahal saat itu belum ada pemindahan resmi apapun untuk item itu di Accurate — stok itu sisa dari sebelum migrasi sistem. Dicek ke histori Agustus penuh, item ini baru pertama kali tercatat `Receive` (via Excel) pada 7 Agustus. **Tool ini, kalau menghitung validitas penjualan tanggal 2 Agustus untuk item ini, akan menyatakan tidak valid** — konsisten dengan kesimpulan manual client.

Namun ada 1 batasan logika yang perlu disadari: validitas tool cuma mengecek **"pernah ada Receive sejak 1 Agustus"**, tanpa mengecek apakah Receive itu terjadi **sebelum** tanggal penjualan yang sedang dicek. Untuk pemakaian harian real-time (kasus pemakaian utama tool ini) ini tidak jadi masalah, karena barang secara alami selalu diterima sebelum penjualannya diproses hari itu juga. Tapi kalau tool dipakai untuk mengecek ULANG tanggal lama setelah histori bulan itu sudah lengkap semua, ada kemungkinan kecil salah tandai valid untuk transaksi yang **saat itu** sebenarnya belum valid. Di luar scope v1.

### 3.3b Verifikasi tambahan ke transaksi resmi Accurate (4 transfer, 3 cabang, tanggal 31/8)

Diverifikasi ulang dengan membandingkan Excel `Receive` tanggal 31/8 ke transaksi "Item Transfer" resmi di Accurate (screenshot langsung dari sistem client):

- **Cabang Losari** (Transfer No. 1375, 5 barang): **cocok 100%** — nama & qty di Excel persis sama dengan transfer resmi.
- **Cabang Madiun & Ponorogo** (Transfer No. 1378-1381, total 102 barang): **Receive = 0 semua di Excel**, padahal transfer resminya ada di Accurate.

Penyebab paling masuk akal: transfer-transfer ke Madiun & Ponorogo itu baru diinput ke Accurate **belakangan** (backdated ke tanggal 31/8, tapi baru benar-benar dimasukkan beberapa hari setelahnya) — jadi Excel yang sudah di-export lebih dulu otomatis tidak memuatnya. Ini dikonfirmasi juga dari notes client sendiri, yang menyebut belum sempat update pencatatan sejak tanggal 30 Agustus dan ada "2 cabang baru" transaksinya di tanggal 31.

**Implikasi:** Excel adalah snapshot pada waktu di-export — kalau ada input susulan/backdated di Accurate setelah itu, Excel (dan histori tool yang di-backfill darinya) tidak akan otomatis ter-update. Solusinya bukan mengubah logika tool, tapi **export ulang Excel & backfill ulang** setiap kali ada input susulan semacam ini. Dicatat sebagai keterbatasan yang disadari, bukan bug.

### 3.4 Rencana ke depan: kemungkinan ganti sumber data

Client berencana suatu saat mengumpulkan data Pemindahan Barang langsung dari Accurate secara manual (screenshot satu-satu, bertahap) sebagai sumber yang lebih akurat dibanding Excel. Tool sudah disiapkan untuk skenario ini: ada fitur **"hapus semua data pemindahan"** (di tab Kelola Data Pemindahan) yang membuang total hasil backfill dari Excel tanpa mengganggu struktur tabel lain, sehingga kalau saatnya tiba, data bisa diganti total dengan sumber yang lebih akurat tanpa perlu bongkar ulang aplikasi.

## 4. Dua Format Laporan Excel

Setiap cabang selalu memakai satu format yang sama secara konsisten sepanjang waktu (tidak pernah campur):

**Format A — `Laporan Stok - Movement Stock`** (mayoritas cabang)
| SKU | Nama Barang | Category | Stok Awal | **Receive** | Retur | Transfer In | **Penjualan** | Transfer Out | Waste | Penyesuaian | Stok Akhir |
|---|---|---|---|---|---|---|---|---|---|---|---|
- Kolom SKU ditulis sebagai formula (`="A01"`) — ditangani otomatis oleh parser.
- Baris tanpa SKU (mis. baris kategori "SLOKI") dan baris "TOTAL" diabaikan.
- Kolom `Transfer In`/`Transfer Out`: dicek ke ratusan file sepanjang Agustus, isinya 0 di semua baris tanpa terkecuali. Client menduga ini representasi pemindahan antar-cabang (bukan dari pusat). Diabaikan di v1.

**Format B — `Daftar Stok`** (sebagian kecil cabang)
| SKU | Nama | Jenis | Awal | **Masuk** | **Terjual** | Akhir | Satuan |
|---|---|---|---|---|---|---|---|
- `Masuk` = `Receive`, `Terjual` = `Penjualan` — representasi yang sama, dikonfirmasi client.

Sebagian file punya periode berupa rentang tanggal (mis. `01/08/2026 - 02/08/2026`) — dicatat memakai tanggal AWAL rentang.

## 5. Reliabilitas Kunci Pencocokan

- **SKU** dipakai sebagai kunci pencocokan utama, per cabang — terbukti stabil selama sumber datanya satu format laporan yang sama.
- **Nama cabang bisa berbeda antara nama file dan isi file.** File bernama "Puri" isinya selalu menyebut cabang resmi "Wijaya Kusuma"; file bernama "Mayjen" isinya selalu "Magersari". Ini alias/nama panggilan, bukan salah ketik. **Tool selalu memakai nama cabang dari isi file** (bukan nama file), jadi ini otomatis ditangani benar.

## 6. Alur Kerja Sistem

### 6.1 Backfill (sekali di awal, atau kapan saja ada file lama tambahan)
Upload banyak file Excel sekaligus (lewat CLI atau tab web). Tool deteksi format tiap file otomatis, ambil baris `Receive`/`Masuk` > 0, simpan sebagai record pemindahan. **Anti-duplikat otomatis**: kombinasi (cabang, SKU, tanggal, qty) yang identik tidak akan tersimpan dobel walau file yang sama diproses berkali-kali atau data yang sama masuk lewat jalur berbeda.

### 6.2 Pengecekan harian (rutin, tiap hari)
1. Upload 1 file Excel penjualan cabang hari itu.
2. Tool filter baris `Penjualan`/`Terjual` > 0, cek tiap barang ke database: pernah dipindah ke cabang ini sejak 1 Agustus 2026?
3. **Output:** qty asli kalau valid, qty diganti **0** kalau tidak valid (barang tetap muncul di tabel, tidak dihapus).
4. **Checkbox opsional (default aktif):** file yang sama sekaligus dipakai menambah histori pemindahan (dedup otomatis). **Kalau dicentang lalu dimatikan lagi, record yang sudah sempat tersimpan dari file itu otomatis dihapus lagi** (di-tracking per file yang sedang diupload, tidak menyentuh data dari sumber lain) — jadi kendali penuh di tangan client, bukan cuma "berhenti menambah".
5. **Cek cepat 1 barang tanpa file**: form terpisah — pilih cabang & barang dari dropdown, isi qty, langsung dapat hasil valid/tidak. Berguna untuk pengecekan spontan tanpa perlu file excel.

### 6.3 Kelola data pemindahan (CRUD)
- Tambah/edit/hapus record manual — dropdown barang (bukan ketik bebas), wajib isi tanggal.
- **Klasifikasi per tanggal**: pilih cabang, lihat tabel ringkas semua tanggal yang punya record pemindahan. Pilih 1 tanggal + klik "Lihat Detail" untuk menampilkan rincian barangnya (tidak langsung ditampilkan semua, supaya ringan).
- **Pencarian & pagination**: tabel "Data pemindahan tersimpan" sekarang punya kotak cari (nama barang/SKU) dan navigasi halaman (50 baris/halaman) — sebelumnya cuma menampilkan 200 baris pertama tanpa cara melihat sisanya.
- **Hapus semua data pemindahan** (dengan konfirmasi eksplisit): untuk skenario ganti total sumber data (lihat §3.4).

### 6.4 Riwayat Import
Log semua file yang pernah diproses tool: nama file, waktu diproses, cabang, tanggal data, jumlah record baru vs duplikat.

### 6.5 Autentikasi
Satu user terdaftar: `admin` / `ujangadmin`. Bisa dioverride lewat environment variable/Streamlit secrets kalau mau diganti tanpa ubah kode.

## 7. Contoh Konkret Alur & Output

Input — excel penjualan cabang Losari hari ini, setelah difilter (`Penjualan > 0`):

| SKU | Nama Barang | Penjualan |
|---|---|---|
| A01 | Anggur Merah Biasa | 5 |
| A09 | Anggur Ginseng Intisari | 3 |
| WKY12 | Jack Daniels 700ml | 2 |

Tool cek ke database histori pemindahan (cabang Losari, sejak 1 Agustus): A01 dan A09 ketemu pernah di-Receive, WKY12 tidak ketemu.

Output:

| SKU | Nama Barang | Qty Terjual (asli) | Valid? | Qty Output |
|---|---|---|---|---|
| A01 | Anggur Merah Biasa | 5 | Ya | **5** |
| A09 | Anggur Ginseng Intisari | 3 | Ya | **3** |
| WKY12 | Jack Daniels 700ml | 2 | Tidak | **0** |

Client input ke Accurate barang dengan Qty Output > 0, lewati yang 0.

## 8. Deployment

**Web deploy gratis: Streamlit Community Cloud (aplikasi) + Turso (database).**

Alasan pakai 2 layanan terpisah: Streamlit Community Cloud gratis untuk menjalankan aplikasinya, tapi storage lokalnya tidak dijamin persisten (bisa terhapus kapan saja platform reboot app). Karena tool ini nilainya ada di data historisnya, database dipisah ke Turso — database cloud gratis, SQLite-compatible, persisten selamanya, tidak terikat siklus hidup aplikasi.

**Konsekuensi yang diterima:** app akan "tidur" kalau tidak diakses 12 jam — begitu dibuka lagi perlu 1 klik "bangunkan" (~30 detik). Data tidak ikut hilang karena tersimpan di Turso, bukan di app. Ini trade-off yang disepakati demi tetap gratis penuh, cocok untuk pola pakai tool ini (dibuka manual oleh client, bukan diakses publik 24 jam).

Langkah deploy lengkap ada di `README.md` bagian "Deploy ke Web".

## 9. Di Luar Scope (v1)

- Tracking sisa stok berjalan (tool ini cuma cek "pernah dipindah atau belum", bukan menghitung sisa stok).
- Urutan waktu presisi antara tanggal Receive dan tanggal penjualan yang dicek (lihat §3.3) — aman untuk pemakaian harian, berisiko kecil untuk audit ulang tanggal lama.
- Integrasi langsung ke Accurate (tidak bisa export data, dikonfirmasi client) — tool berdiri sendiri, hasil diinput manual oleh client ke Accurate.
- Multi-user dengan akun/role berbeda (cuma 1 user: admin).

## 10. Status Implementasi

Prototype (Python + Streamlit + Turso) sudah dibangun dan diuji dengan data Agustus asli (636 file, 0 gagal parse; dedup terverifikasi — backfill dijalankan 2x, kedua kalinya 0 record baru/semua terdeteksi duplikat). Fitur yang sudah jalan: autentikasi, backfill batch (CLI & web), pengecekan harian dengan auto-capture opsional, CRUD dengan dropdown + klasifikasi per tanggal + hapus total, riwayat import.

**Catatan uji:** koneksi ke Turso ditulis sesuai dokumentasi resmi terbaru, tapi belum bisa diuji langsung ke server Turso sungguhan dalam proses pengembangan ini (keterbatasan akses jaringan di lingkungan development). Wajib diuji ulang setelah akun Turso asli dibuat, sebelum dipakai produksi.

Karena data operasional yang ada cuma cakupan Agustus, disertakan 2 file dummy (tanggal di luar Agustus) untuk demo tab Pengecekan Harian — lihat `dummy_harian/` di paket.

## 11. Rencana Selanjutnya

1. Setup akun Turso, migrasi database dari mode lokal ke cloud, uji ulang seluruh fitur di lingkungan production.
2. Deploy ke Streamlit Community Cloud sesuai panduan di README.
3. Client mengirim data terbaru secara bertahap untuk cabang-cabang yang belum sempat dianalisis; setiap data baru diverifikasi dulu strukturnya sebelum diintegrasikan. Desain tool modular (parser terpisah per format), jadi penambahan format baru tidak memerlukan rombak ulang.
4. Kalau client nanti selesai mengumpulkan data Pemindahan Barang manual dari Accurate (lihat §3.4), evaluasi ulang apakah mau mengganti total sumber data lewat fitur "hapus semua data pemindahan".

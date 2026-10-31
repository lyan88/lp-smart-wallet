# Top LP Smart Wallet Finder

Bot **read-only** untuk menemukan wallet LP pintar di Solana. Bot mencari
wallet yang menambah likuiditas di pool yang sedang menang, lalu menilai
apakah fee LP lebih besar daripada impermanent loss (IL). Kandidat terbaik
dikirim ke Telegram lewat cron.

**Tidak memegang private key dan tidak mengirim transaksi.** Semua data
diambil dari API publik dan RPC Solana.

## Apa yang dianggap "smart LP"

Sebuah wallet masuk radar kalau memenuhi salah satu bukti LP:

1. **Transaksi LP terverifikasi** — instruksi deposit/withdraw yang dikenal,
   atau perubahan saldo dua token pool yang searah.
2. **Kepemilikan LP token** — wallet muncul sebagai pemegang LP token pool
   Raydium CPMM / AMM v4 (bukti paling kuat).

Lalu posisinya dinilai dengan proxy **fee > IL**. Wallet dengan skor tinggi
dan bukti LP yang masih segar masuk ranking dan alert.

## Sumber data

| Data | Sumber | Endpoint |
|---|---|---|
| Pool trending, harga, volume, TVL | GeckoTerminal | `/networks/solana/trending_pools` |
| Trade terbaru | GeckoTerminal | `/networks/solana/pools/{addr}/trades` |
| Transaksi LP | RPC Solana | `getSignaturesForAddress`, `getTransaction` |
| Pemegang LP token | RPC Solana | `getAccountInfo`, `getTokenSupply`, `getTokenLargestAccounts`, `getMultipleAccounts` |
| Tautan cek manual | Solscan | hanya link di alert |

Tidak ada API key untuk GeckoTerminal. RPC publik bisa dipakai, tetapi
`getTokenLargestAccounts` sangat dibatasi; lihat bagian **RPC**.

## Cara kerja satu putaran

1. Ambil pool trending Solana, urutkan volume 24 jam.
2. Tandai **pool menang** (ramai, likuid, tidak sedang dump).
3. Tarik trade terbaru untuk menemukan wallet aktif.
4. Deteksi aksi LP on-chain:
   - discriminator instruksi deposit/withdraw yang sudah diverifikasi;
   - fallback: dua token pool bergerak searah (dua-duanya turun = add,
     dua-duanya naik = remove).
   - Transaksi satu sisi tanpa discriminator **diabaikan** supaya buy/sell
     PumpSwap tidak salah dianggap LP.
5. Enumerasi pemegang LP token terbesar untuk pool Raydium CPMM / AMM v4.
6. Hitung fee harian, IL, edge, skor LP, skor trading, dan tier.
7. Antrekan alert ke Telegram sesuai kuota dan cooldown.

## Filter pool menang

| Filter | Default | Arti |
|---|---|---|
| Volume 24 jam | ≥ $100.000 | pool cukup ramai |
| TVL | ≥ $20.000 | ada likuiditas |
| V/TVL | ≥ 2.0x | perputaran modal tinggi |
| Drop 1 jam | > -25% | tidak sedang dump |
| Drop 24 jam | > -60% | tidak sedang rugi parah |
| Buyer 1 jam | ≥ 10 | masih ada pembeli |

Semua angka bisa diubah di `.env`.

## Fee vs impermanent loss

Proxy yang dipakai konsisten, bukan laporan PnL akuntansi:

```text
fee_yield_daily = (volume_24j / TVL) x fee_rate
IL              = 2 x sqrt(r) / (1 + r) - 1      # r = harga sekarang / harga masuk
edge            = fee_yield_daily x hari - |IL|
```

- `fee_rate` diambil dari GeckoTerminal; kalau tidak ada, pakai
  `DEFAULT_FEE_RATE` (0,3%).
- Posisi yang baru beberapa menit dinormalkan ke **minimal 1 hari** supaya
  fee harian tetap terukur.
- Umur posisi dibatasi `LP_MAX_HOLD_DAYS` (30 hari).
- Untuk pemegang LP token, harga masuk tidak diketahui, jadi edge memakai
  proxy 1 hari dan perubahan harga 24 jam.

Positif berarti fee menutup IL. Ini penyaring awal, bukan jaminan profit.

## Skor

**Skor LP** (0–100):

| Komponen | Bobot | Sumber |
|---|---|---|
| Aktivitas | 20% | jumlah add + jumlah posisi LP yang dipegang |
| Kualitas pool | 30% | skor pool rata-rata |
| Edge fee-IL | 30% | edge / 5% dianggap penuh |
| Recency | 20% | makin baru makin tinggi |

**Skor trading** (0–100): win rate 35%, rata-rata peak ROI 30%, aktivitas
15%, PnL 20%.

**Skor gabungan** = 70% skor LP + 30% skor trading. Wallet tanpa bukti LP
dibatasi maksimal 45. Tier: **S ≥ 85**, **A ≥ 70**, **B ≥ 55**,
**C ≥ 40**, sisanya **D**.

## Enumerasi pemegang LP token

Ini bukti terkuat dan tidak bergantung pada tebakan transaksi. Layout akun
pool sudah diverifikasi dari mainnet:

| DEX | Program | `lp_mint` di data akun | Status |
|---|---|---|---|
| Raydium CPMM | `CPMMoo8...` | offset 136 | LP token SPL, didukung |
| Raydium AMM v4 | `675kPX9...` | offset 464 | LP token SPL, didukung |
| PumpSwap | `pAMMBay...` | offset 107 | mint ada, supply SPL 0 → dilewati |
| Orca / Raydium CLMM / Meteora DLMM | — | posisi NFT | dilewati |

Bot mengambil 20 pemegang terbesar, menggabungkan beberapa token account
milik wallet yang sama, lalu menyimpan share dan estimasi nilai posisi
(`share x TVL`). Holder dengan share/nilai signifikan memicu alert
**TOP LP DETECTED**.

### RPC

RPC publik gratis (`publicnode`, `api.mainnet-beta`, `drpc`) sering menolak
`getTokenLargestAccounts` dengan 403/429. Scanner tetap jalan dan melewati
enumerasi holder kalau RPC menolak, tetapi untuk hasil stabil gunakan
endpoint gratis dari Helius/QuickNode:

```bash
RPC_URL=https://mainnet.helius-rpc.com/?api-key=...
RPC_BUDGET_PER_RUN=300
```

## Pasang

```bash
cd /home/ubuntu/lp-smart-wallet
cp .env.example .env
nano .env                  # isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID
python3 doctor.py          # cek database, API, RPC, Telegram
python3 scanner.py --once --dry-run --verbose
```

Tidak ada dependensi luar; hanya **Python 3.8+** dan stdlib.

## Cron

```cron
# scan tiap 5 menit
*/5 * * * * cd /opt/lp-smart-wallet && flock -n locks/scan.lock python3 scanner.py >> logs/scan.log 2>&1
# segarkan harga + skor tiap 15 menit
*/15 * * * * cd /opt/lp-smart-wallet && flock -n locks/track.lock python3 track.py >> logs/track.log 2>&1
# laporan harian 07:00 UTC (14:00 WIB)
0 7 * * * cd /opt/lp-smart-wallet && python3 report.py --telegram >> logs/report.log 2>&1
```

Atau pasang otomatis:

```bash
sudo adduser --system --group lpbot
sudo ./deploy/install.sh
```

## Perintah

```bash
python3 scanner.py --once --dry-run --verbose   # satu putaran, tanpa kirim Telegram
python3 scanner.py --once                       # satu putaran, kirim alert
python3 scanner.py --wallet <alamat>            # profil satu wallet
python3 scanner.py --test-telegram              # pesan uji
python3 scanner.py --summary                    # isi database
python3 track.py --status                       # ranking + holder terbesar
python3 track.py                                # segarkan harga dan skor
python3 report.py --dry-run                     # laporan harian
python3 doctor.py                               # diagnosa
python3 -m unittest discover -s tests -v        # 71 unit test
```

## Konfigurasi penting

| Variabel | Default | Arti |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | token bot dari BotFather |
| `TELEGRAM_CHAT_ID` | — | chat/grup tujuan |
| `ALERT_LANG` | `id` | `id`, `en`, atau `both` |
| `DRY_RUN` | `false` | `true` = cetak saja |
| `RPC_URL` | publicnode | endpoint RPC utama |
| `RPC_BUDGET_PER_RUN` | 90 | batas panggilan RPC per proses |
| `MIN_POOL_VOL_USD` | 100000 | volume minimal pool |
| `MIN_V_TVL` | 2.0 | perputaran minimal |
| `MIN_LP_SIZE_USD` | 200 | ukuran minimal event LP untuk alert |
| `LP_HOLDER_SCAN_LIMIT` | 3 | pool holder yang dipindai per putaran |
| `LP_HOLDER_ALERT_MIN_PCT` | 0.02 | share minimal untuk alert holder (2%) |
| `ALERT_MIN_SCORE` | 70 | skor minimal alert wallet |
| `ALERT_LP_EVENTS` | `true` | alert saat ada LP add baru |
| `ALERT_LP_HOLDERS` | `true` | alert saat ada holder LP baru |

Daftar lengkap ada di `.env.example`.

## Batasan yang perlu diketahui

- **Proxy, bukan akuntansi.** Fee dan IL dihitung dari volume/TVL dan
  perubahan harga, bukan dari posisi aktual per wallet. Anggap ini penyaring
  awal.
- **Holder hanya untuk Raydium CPMM / AMM v4.** DEX dengan posisi NFT tidak
  bisa dienumerasi dari SPL mint. PumpSwap punya mint tetapi supply SPL-nya 0.
- **Hanya 20 holder terbesar** yang dikembalikan `getTokenLargestAccounts`.
  Wallet kecil di luar top 20 tidak terlihat lewat jalur ini.
- **RPC publik membatasi.** Tanpa RPC khusus, sebagian putaran akan melewati
  enumerasi holder. Ini tidak menghentikan scanner.
- **GeckoTerminal free tier dibatasi.** Limiter menyimpan jeda antar
  panggilan di `data/rate_*.json`.
- **Deteksi transaksi sengaja konservatif.** Lebih baik kehilangan sebagian
  sinyal daripada salah menyebut trader sebagai LP.
- Selalu cek pool dan kontrak sendiri sebelum mengambil keputusan.

## Struktur

```text
scanner.py     orkestrasi satu putaran + alert
track.py       segarkan harga, edge, skor, status
report.py      laporan harian
doctor.py      diagnosa
sources.py     GeckoTerminal + RPC (termasuk fetch_lp_holders)
solana_lp.py   klasifikasi transaksi LP
programs.py    registry DEX, discriminator, offset lp_mint
scoring.py     fee, IL, edge, skor
filters.py     filter pool/wallet + format
db.py          SQLite: pool, trade, wallet, event, holding, alert
render.py      pesan Telegram HTML
telegram.py    transport Telegram
i18n.py        label id/en
tests/         71 unit test
deploy/        install, cron, logrotate
```

## Lisensi

MIT — lihat `LICENSE`.

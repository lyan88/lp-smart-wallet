#!/usr/bin/env bash
# Pasang Top LP Smart Wallet Finder di /opt/lp-smart-wallet dan aktifkan cron
# (scan tiap 5 menit, tracker tiap 15 menit, laporan 07:00 UTC).
# Jalankan sebagai root dari folder proyek:
#
#   sudo ./deploy/install.sh
#
# Opsi:
#   --no-cron     pasang berkas saja, jangan sentuh /etc/cron.d
#   --force       timpa instalasi yang sudah ada

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DST="/opt/lp-smart-wallet"
RUN_USER="lpbot"
INSTALL_CRON=1
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --no-cron) INSTALL_CRON=0 ;;
    --force) FORCE=1 ;;
    *) echo "argumen tidak dikenal: $arg" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "jalankan sebagai root: sudo $0" >&2
  exit 1
fi

if [[ -e "$DST" && $FORCE -eq 0 ]]; then
  echo "$DST sudah ada. Pakai --force kalau memang mau menimpa." >&2
  exit 1
fi

if ! id "$RUN_USER" >/dev/null 2>&1; then
  echo "user '$RUN_USER' belum ada; buat dulu:" >&2
  echo "  sudo adduser --system --group $RUN_USER" >&2
  exit 1
fi

if [[ ! -f "$SRC/.env" ]]; then
  echo "PERINGATAN: $SRC/.env tidak ada. Salin .env.example lalu isi token Telegram."
fi

echo "==> menyalin berkas ke $DST"
mkdir -p "$DST"
for f in \
  config.py net.py sources.py filters.py db.py telegram.py i18n.py \
  base58.py programs.py solana_lp.py scoring.py render.py \
  scanner.py track.py report.py doctor.py README.md .env.example LICENSE; do
  install -m 0644 -o "$RUN_USER" -g "$RUN_USER" "$SRC/$f" "$DST/$f"
done
mkdir -p "$DST/tests" "$DST/deploy" "$DST/data" "$DST/logs" "$DST/locks"
for f in "$SRC"/tests/test_*.py; do
  install -m 0644 -o "$RUN_USER" -g "$RUN_USER" "$f" "$DST/tests/"
done
install -m 0755 -o "$RUN_USER" -g "$RUN_USER" \
  "$SRC/deploy/install.sh" "$DST/deploy/install.sh"
install -m 0644 -o "$RUN_USER" -g "$RUN_USER" \
  "$SRC/deploy/lp-smart-wallet.cron" "$DST/deploy/lp-smart-wallet.cron"
install -m 0644 -o "$RUN_USER" -g "$RUN_USER" \
  "$SRC/deploy/logrotate-lp-smart-wallet" \
  "$DST/deploy/logrotate-lp-smart-wallet"

if [[ -f "$SRC/.env" ]]; then
  install -m 0600 -o "$RUN_USER" -g "$RUN_USER" "$SRC/.env" "$DST/.env"
elif [[ -f "$DST/.env" ]]; then
  chown "$RUN_USER:$RUN_USER" "$DST/.env"
  chmod 600 "$DST/.env"
fi

chown -R "$RUN_USER:$RUN_USER" "$DST/data" "$DST/logs" "$DST/locks" "$DST/tests"
chmod 755 "$DST"

echo "==> uji cepat"
sudo -u "$RUN_USER" bash -lc "cd $DST && python3 -m unittest discover -s tests -q"
sudo -u "$RUN_USER" bash -lc "cd $DST && python3 doctor.py --offline" || {
  echo "doctor gagal; cron TIDAK diaktifkan." >&2
  exit 1
}

if [[ -d /etc/logrotate.d ]]; then
  install -m 0644 "$SRC/deploy/logrotate-lp-smart-wallet" \
    /etc/logrotate.d/lp-smart-wallet
  echo "logrotate: /etc/logrotate.d/lp-smart-wallet"
fi

if [[ $INSTALL_CRON -eq 1 ]]; then
  echo "==> memasang cron"
  if [[ -f /etc/cron.d/lp-smart-wallet ]]; then
    cp /etc/cron.d/lp-smart-wallet \
      "/etc/cron.d/lp-smart-wallet.bak.$(date +%s)"
  fi
  install -m 0644 "$SRC/deploy/lp-smart-wallet.cron" \
    /etc/cron.d/lp-smart-wallet
  echo "cron aktif: /etc/cron.d/lp-smart-wallet"
else
  echo "cron dilewati (--no-cron)"
fi

echo
echo "Selesai."
echo "  log     : $DST/logs/scan.log"
echo "  tracker : $DST/logs/track.log"
echo "  laporan : $DST/logs/report.log"
echo "  database: $DST/data/lp_smart_wallet.db"
echo "  uji     : sudo -u $RUN_USER python3 $DST/scanner.py --once --dry-run --verbose"

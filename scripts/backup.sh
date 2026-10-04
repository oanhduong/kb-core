#!/usr/bin/env bash
# Backup Postgres (DB retailkb + DB langfuse) + volume file upload — chạy tay hoặc cron (F5).
#   ./scripts/backup.sh                   # backup vào ./backups, giữ BACKUP_KEEP_DAYS ngày (mặc định 14)
#   cron: 15 2 * * * cd /opt/retailkb && ./scripts/backup.sh >> backups/backup.log 2>&1
# Định dạng pg_dump custom (-Fc) -> restore chọn lọc được bằng pg_restore. File upload cần cho reindex/
# rebuild graph nên được backup cùng lúc (cùng timestamp).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
COMPOSE="${COMPOSE:-docker compose}"
DB="${POSTGRES_DB:-retailkb}"
USER_="${POSTGRES_USER:-retail}"
KEEP="${BACKUP_KEEP_DAYS:-14}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="backups"
mkdir -p "$OUT"

echo "[$(date -u +%FT%TZ)] backup bắt đầu ($TS)"
$COMPOSE exec -T postgres pg_dump -U "$USER_" -d "$DB" -Fc --no-owner > "$OUT/retailkb-$TS.dump"
$COMPOSE exec -T postgres pg_dump -U "$USER_" -d langfuse -Fc --no-owner > "$OUT/langfuse-$TS.dump" \
  || echo "  (bỏ qua langfuse: DB chưa tồn tại?)"
# file gốc khách hàng (volume uploads) — đọc qua container api để không phụ thuộc đường dẫn volume trên host
$COMPOSE exec -T api tar -C /app/data -czf - uploads > "$OUT/uploads-$TS.tar.gz"

for f in "$OUT/retailkb-$TS.dump" "$OUT/uploads-$TS.tar.gz"; do
  [ -s "$f" ] || { echo "LỖI: $f rỗng — backup thất bại" >&2; exit 1; }
done
sha256sum "$OUT"/*-"$TS".* > "$OUT/SHA256SUMS-$TS" 2>/dev/null || shasum -a 256 "$OUT"/*-"$TS".* > "$OUT/SHA256SUMS-$TS"
find "$OUT" -maxdepth 1 -type f \( -name '*.dump' -o -name '*.tar.gz' -o -name 'SHA256SUMS-*' \) -mtime +"$KEEP" -print -delete
echo "[$(date -u +%FT%TZ)] backup xong: $(ls -1 "$OUT"/*-"$TS".* | tr '\n' ' ')"

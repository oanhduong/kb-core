#!/usr/bin/env bash
# Restore từ 1 bộ backup (F5). DỪNG api/worker trước để không ghi chen trong lúc restore.
#   ./scripts/restore.sh 20261003T021500Z            # restore DB retailkb + uploads (+ langfuse nếu có)
#   ./scripts/restore.sh 20261003T021500Z --db-only
# Sau restore: make up (migration tự chạy nếu bản backup cũ hơn code hiện tại).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
TS="${1:?Cách dùng: scripts/restore.sh <TIMESTAMP> [--db-only]}"
MODE="${2:-}"
COMPOSE="${COMPOSE:-docker compose}"
DB="${POSTGRES_DB:-retailkb}"
USER_="${POSTGRES_USER:-retail}"
IN="backups"
[ -f "$IN/retailkb-$TS.dump" ] || { echo "Không thấy $IN/retailkb-$TS.dump" >&2; exit 1; }
if [ -f "$IN/SHA256SUMS-$TS" ]; then
  (sha256sum -c "$IN/SHA256SUMS-$TS" 2>/dev/null || shasum -a 256 -c "$IN/SHA256SUMS-$TS") || { echo "Checksum sai — dừng" >&2; exit 1; }
fi

echo "Dừng api, worker, admin-ui ..."
$COMPOSE stop api worker admin-ui >/dev/null
$COMPOSE up -d postgres >/dev/null

echo "Restore DB $DB ..."
$COMPOSE exec -T postgres psql -U "$USER_" -d postgres -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB' AND pid <> pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS $DB;" -c "CREATE DATABASE $DB;"
$COMPOSE exec -T postgres pg_restore -U "$USER_" -d "$DB" --no-owner --exit-on-error < "$IN/retailkb-$TS.dump"

if [ -f "$IN/langfuse-$TS.dump" ] && [ "$MODE" != "--db-only" ]; then
  echo "Restore DB langfuse ..."
  $COMPOSE exec -T postgres psql -U "$USER_" -d postgres -c "DROP DATABASE IF EXISTS langfuse;" -c "CREATE DATABASE langfuse;"
  $COMPOSE exec -T postgres pg_restore -U "$USER_" -d langfuse --no-owner < "$IN/langfuse-$TS.dump" || true
fi

if [ "$MODE" != "--db-only" ] && [ -f "$IN/uploads-$TS.tar.gz" ]; then
  echo "Restore file upload ..."
  $COMPOSE run --rm -T --no-deps --entrypoint sh api -c "rm -rf /app/data/uploads/* && tar -C /app/data -xzf -" < "$IN/uploads-$TS.tar.gz"
fi
echo "Khởi động lại ..."
$COMPOSE up -d
echo "Restore xong từ bộ $TS."

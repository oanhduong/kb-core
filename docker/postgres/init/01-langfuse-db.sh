#!/bin/bash
# Lần khởi tạo volume đầu tiên: tạo database riêng cho Langfuse (cùng Postgres, tách DB).
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  SELECT 'CREATE DATABASE ${LANGFUSE_DB:-langfuse}' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${LANGFUSE_DB:-langfuse}')\gexec
SQL

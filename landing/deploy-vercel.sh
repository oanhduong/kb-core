#!/usr/bin/env sh
# Deploy thư mục landing/ lên Vercel. Cần VERCEL_TOKEN (environment secret) hoặc đã `vercel login` trên máy.
# Dùng: sh landing/deploy-vercel.sh            -> preview URL
#       sh landing/deploy-vercel.sh --prod     -> production
set -e
cd "$(dirname "$0")"
if command -v vercel >/dev/null 2>&1; then CLI=vercel; else CLI="npx --yes vercel@latest"; fi
TOKEN_ARG=""; [ -n "$VERCEL_TOKEN" ] && TOKEN_ARG="--token $VERCEL_TOKEN"
$CLI deploy --yes $TOKEN_ARG "$@"

#!/usr/bin/env sh
# Build web app KB Explorer (thư mục web/) và đặt dưới landing/dist/app để Vercel phục vụ tại /app.
# API_URL: địa chỉ backend FastAPI (vd https://api.ten-mien.vn). Bỏ trống = gọi /api cùng origin.
# Backend phải cho phép origin của trang này trong CORS_ORIGINS.
set -e
cd "$(dirname "$0")/../web"
if [ ! -d node_modules ]; then npm ci --no-audit --no-fund --loglevel=error; fi
VITE_BASE=/app/ VITE_API_BASE="${API_URL:-}" npm run build
rm -rf ../landing/dist/app
mkdir -p ../landing/dist
cp -r dist ../landing/dist/app
echo "landing/dist/app: web app, API=${API_URL:-/api}"

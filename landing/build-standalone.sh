#!/usr/bin/env sh
# Đóng gói game (viết theo chuẩn artifact, không có <html>/<head>) thành trang tĩnh đầy đủ để tự host.
# Dùng: APP_URL=https://app.ten-mien.vn sh landing/build-standalone.sh   -> landing/dist/index.html
# APP_URL: địa chỉ web app nếu app chạy ở nơi khác. Bỏ trống = /app trên cùng site (build-app.sh).
set -e
cd "$(dirname "$0")"
mkdir -p dist
desc="Chơi 1 phút: thả đống file vào kho, hỏi gì cũng ra số, mọi câu trả lời có nguồn."
app=$(printf '%s' "${APP_URL:-}" | sed 's/[&|\\]/\\&/g')
{
  printf '<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
  printf '<meta name="description" content="%s"><meta property="og:title" content="Hỏi Số"><meta property="og:description" content="%s"><meta name="theme-color" content="#9ed7ff">' "$desc" "$desc"
  printf '<style>:root{color-scheme:light}body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%%}[hidden]{display:none!important}</style></head><body>'
  sed "s|%%APP_URL%%|$app|g" index.html
  printf '</body></html>'
} > dist/index.html
echo "dist/index.html: $(wc -c < dist/index.html) bytes, APP_URL=${APP_URL:-/app}"

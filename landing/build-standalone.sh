#!/usr/bin/env sh
# Đóng gói index.html (viết theo chuẩn artifact, không có <html>/<head>) thành trang tĩnh đầy đủ để tự host.
# Dùng: sh landing/build-standalone.sh  -> landing/dist/index.html
set -e
cd "$(dirname "$0")"
mkdir -p dist
{
  printf '<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
  printf '<meta name="description" content="Thả file Excel vào, hỏi bằng tiếng Việt, nhận biểu đồ và báo cáo với số liệu tính đúng, có nguồn.">'
  printf '<style>:root{color-scheme:light}body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%%}[hidden]{display:none!important}</style></head><body>'
  cat index.html
  printf '</body></html>'
} > dist/index.html
echo "dist/index.html: $(wc -c < dist/index.html) bytes"

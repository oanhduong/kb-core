#!/usr/bin/env sh
# Đóng gói trang (viết theo chuẩn artifact, không có <html>/<head>) thành trang tĩnh đầy đủ để tự host.
# Dùng: sh landing/build-standalone.sh        -> landing/dist/index.html       (trang chủ: game Hỏi Số)
#       sh landing/build-standalone.sh chat   -> landing/dist/chat/index.html  (bản thử với file Excel thật + A/B)
set -e
cd "$(dirname "$0")"
sub="${1:-}"
src="${sub:+$sub/}index.html"
out="dist/${sub:+$sub/}index.html"
mkdir -p "$(dirname "$out")"
case "$sub" in
  chat) desc="Thả file Excel vào, hỏi bằng tiếng Việt, nhận biểu đồ và báo cáo với số liệu tính đúng, có nguồn." ;;
  *)    desc="Chơi 1 phút: thả đống file vào kho, hỏi gì cũng ra số, mọi câu trả lời có nguồn. Không cần đăng ký." ;;
esac
{
  printf '<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
  printf '<meta name="description" content="%s"><meta property="og:title" content="Hỏi Số"><meta property="og:description" content="%s"><meta name="theme-color" content="#9ed7ff">' "$desc" "$desc"
  printf '<style>:root{color-scheme:light}body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%%}[hidden]{display:none!important}</style></head><body>'
  cat "$src"
  printf '</body></html>'
} > "$out"
echo "$out: $(wc -c < "$out") bytes"

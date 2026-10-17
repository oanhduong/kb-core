#!/usr/bin/env sh
# Đóng gói index.html (viết theo chuẩn artifact, không có <html>/<head>) thành trang tĩnh đầy đủ để tự host.
# Dùng: sh landing/build-standalone.sh        -> landing/dist/index.html      (trang A/B + waitlist)
#       sh landing/build-standalone.sh wow    -> landing/dist/wow/index.html  (trang chơi 3D)
set -e
cd "$(dirname "$0")"
sub="${1:-}"
src="${sub:+$sub/}index.html"
out="dist/${sub:+$sub/}index.html"
mkdir -p "$(dirname "$out")"
case "$sub" in
  wow) desc="Thả đống file rối vào một kho biết trả lời: hỏi bằng tiếng Việt, số tính bằng SQL, báo cáo trong 2 phút, mọi câu có nguồn."; scheme=dark ;;
  *)   desc="Thả file Excel vào, hỏi bằng tiếng Việt, nhận biểu đồ và báo cáo với số liệu tính đúng, có nguồn."; scheme=light ;;
esac
{
  printf '<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
  printf '<meta name="description" content="%s">' "$desc"
  printf '<style>:root{color-scheme:%s}body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%%}[hidden]{display:none!important}</style></head><body>' "$scheme"
  cat "$src"
  printf '</body></html>'
} > "$out"
echo "$out: $(wc -c < "$out") bytes"

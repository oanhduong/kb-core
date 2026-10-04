"""CLI ingest (chạy trong container api, xem Makefile):

    python -m ingest.cli seed [--wait]                          # nạp dữ liệu mẫu data/raw + alias mặc định
    python -m ingest.cli upload FILE [FILE...] [--doc-type price_list] [--store GM-HN01]
                                 [--effective-date 2026-09-01] [--title "Bảng giá tháng 9"] [--replace-mode document] [--wait]
    python -m ingest.cli status JOB_ID

Upload chỉ đăng ký file + tạo job; worker xử lý nền. --wait: chờ và in trạng thái tới khi mọi job xong.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from datetime import date

from sqlalchemy import select

from core.config import get_config
from core.db import session_scope
from core.logging import configure_logging
from core.models import Job
from graph.resolve import default_seed_aliases, upsert_alias
from ingest.service import UploadOptions, register_upload
from worker.queue import job_view

# Tên hiển thị (dùng trong citation) + ngày hiệu lực cho bộ dữ liệu mẫu GreenMart (data/raw).
SEED_META: dict[str, dict] = {
    "01_bang_gia_sku.xlsx": {"title": "Bảng giá tháng 8", "effective_date": date(2026, 8, 1), "doc_type": "price_list"},
    "02_ton_kho_theo_store.xlsx": {"title": "Tồn kho theo cửa hàng", "effective_date": date(2026, 8, 20), "doc_type": "inventory"},
    "03_lich_khuyen_mai.xlsx": {"title": "Lịch khuyến mãi quý 3", "doc_type": "promotion"},
    "04_bang_chiet_khau_theo_bac.xlsx": {"title": "Bảng chiết khấu theo bậc", "doc_type": "price_list"},
    "05_danh_muc_nha_cung_cap.xlsx": {"title": "Danh mục nhà cung cấp", "doc_type": "supplier"},
    "01_catalogue_do_gia_dung.pdf": {"title": "Catalogue đồ gia dụng", "doc_type": "catalog"},
    "02_catalogue_dien_tu_tieu_dung.pdf": {"title": "Catalogue điện tử tiêu dùng", "doc_type": "catalog"},
    "03_hop_dong_nha_cung_cap_dien_may.pdf": {"title": "Hợp đồng nhà cung cấp điện máy", "doc_type": "contract"},
    "04_hop_dong_nha_cung_cap_thuc_pham.pdf": {"title": "Hợp đồng nhà cung cấp thực phẩm", "doc_type": "contract"},
    "05_scan_bien_ban_ban_giao_hang_hoa.pdf": {"title": "Biên bản bàn giao hàng hoá (bản scan)", "doc_type": "contract"},
    "01_poster_khuyen_mai.png": {"title": "Poster khuyến mãi tháng 7", "doc_type": "promotion"},
    "02_bang_gia_viet_tay.png": {"title": "Bảng giá viết tay tại quầy", "doc_type": "price_list", "replace_mode": "none"},
    "03_anh_chup_man_hinh_pos.png": {"title": "Ảnh màn hình POS", "doc_type": "training"},
    "04_bien_hieu_quay.png": {"title": "Biển hiệu quầy", "doc_type": "training"},
    "05_phieu_bao_hanh.png": {"title": "Mẫu phiếu bảo hành", "doc_type": "policy", "replace_mode": "none"},
    "01_training_chao_don_khach_hang.mp3": {"title": "Audio đào tạo chào đón khách hàng", "doc_type": "training"},
    "02_training_xu_ly_doi_tra_tai_quay.mp3": {"title": "Audio đào tạo xử lý đổi trả tại quầy", "doc_type": "training"},
    "03_callcenter_hoi_bao_hanh_san_pham.mp3": {"title": "Ghi âm tổng đài hỏi bảo hành", "doc_type": "training"},
    "01_video_quy_trinh_thanh_toan_quay_thu_ngan.mp4": {"title": "Video hướng dẫn thu ngân", "doc_type": "training"},
    "02_video_xu_ly_loi_pos_hoan_tien.mp4": {"title": "Video xử lý lỗi POS và hoàn tiền", "doc_type": "training"},
}


def _upload(paths: list[str], opts_for, actor: str) -> list[str]:
    cfg = get_config()
    job_ids = []
    with session_scope() as s:
        for path in paths:
            res = register_upload(s, cfg, src=path, filename=os.path.basename(path), opts=opts_for(path), uploaded_by=actor)
            flag = " (đã có — không xử lý lại)" if res.duplicate_of_upload else ""
            print(f"  {res.filename}: document={res.document_id} job={res.job_id}{flag}")
            job_ids.append(res.job_id)
    return job_ids


def _wait(job_ids: list[str], timeout: float = 3600) -> int:
    start = time.monotonic()
    last = ""
    while time.monotonic() - start < timeout:
        with session_scope() as s:
            jobs = [s.get(Job, j) for j in job_ids]
            # job graph_sync phát sinh từ ingest cũng phải xong
            docs = [j.document_id for j in jobs if j and j.document_id]
            graph = s.execute(select(Job).where(Job.type == "graph_sync", Job.document_id.in_(docs))).scalars().all()
            states = [j.status for j in jobs] + [g.status for g in graph]
            summary = " ".join(f"{k}={states.count(k)}" for k in ("queued", "processing", "done", "blocked", "error") if states.count(k))
            if summary != last:
                print(f"  [{int(time.monotonic() - start)}s] {summary}")
                last = summary
            if all(st in ("done", "blocked", "error") for st in states):
                bad = [j for j in jobs if j.status != "done"]
                for j in bad:
                    print(f"  ⚠️ {j.id} ({j.document_id}): {j.status} — {j.error}")
                return 1 if bad else 0
        time.sleep(3)
    print("Hết thời gian chờ — xem trạng thái ở Admin UI hoặc GET /ingest/{job_id}")
    return 2


def main(argv: list[str] | None = None) -> int:
    configure_logging(fmt=os.environ.get("LOG_FORMAT", "text"))
    p = argparse.ArgumentParser(description="Ingest file vào KB RetailKB.")
    sub = p.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("seed")
    ps.add_argument("--dir", default="data/raw")
    ps.add_argument("--only", choices=["docx", "xlsx", "pdf", "image", "audio", "video"], nargs="*",
                    help="Chỉ nạp các thư mục này (mặc định: tất cả)")
    ps.add_argument("--wait", action="store_true")
    pu = sub.add_parser("upload")
    pu.add_argument("files", nargs="+")
    pu.add_argument("--doc-type")
    pu.add_argument("--store")
    pu.add_argument("--effective-date", type=date.fromisoformat)
    pu.add_argument("--title")
    pu.add_argument("--version")
    pu.add_argument("--series-key")
    pu.add_argument("--replace-mode", default="document", choices=["document", "record", "none"])
    pu.add_argument("--wait", action="store_true")
    pst = sub.add_parser("status")
    pst.add_argument("job_id")
    args = p.parse_args(argv)

    if args.cmd == "status":
        with session_scope() as s:
            j = s.get(Job, args.job_id)
            if j is None:
                print("Không có job")
                return 1
            print(job_view(j))
        return 0

    if args.cmd == "seed":
        with session_scope() as s:
            for a in default_seed_aliases():
                upsert_alias(s, a.kind, a.alias, a.canonical, note="seed", actor="seed")
        folders = args.only or ["xlsx", "docx", "pdf", "image", "audio", "video"]
        paths = [f for folder in folders for f in sorted(glob.glob(os.path.join(args.dir, folder, "*"))) if not f.endswith(".md")]
        print(f"Nạp {len(paths)} file mẫu từ {args.dir} ...")
        ids = _upload(paths, lambda path: UploadOptions(**SEED_META.get(os.path.basename(path), {})), "seed")
        return _wait(ids) if args.wait else 0

    if len(args.files) > 1 and args.title:
        print("--title chỉ dùng khi upload 1 file", file=sys.stderr)
        return 2
    opts = UploadOptions(title=args.title, doc_type=args.doc_type, store_code=args.store, effective_date=args.effective_date,
                         version=args.version, series_key=args.series_key, replace_mode=args.replace_mode)
    ids = _upload(args.files, lambda _p: opts, "cli")
    return _wait(ids) if args.wait else 0


if __name__ == "__main__":
    sys.exit(main())

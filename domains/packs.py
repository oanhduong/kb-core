"""Khai báo domain pack. Lõi hệ thống không biết gì về retail; mọi thứ đặc thù nằm ở đây.

- generic: mặc định cho mọi workspace — không graph rule-based (chỉ knowledge graph mở do LLM trích),
  không review bắt buộc, thành viên không có thuộc tính đặc thù.
- retail: bật graph SKU/Store/Promotion/Policy/Supplier (graph/build.py) trên Apache AGE, câu hỏi
  liên quan tiền phải qua supervisor (editor/owner) duyệt khi người hỏi là viewer, thành viên có thể
  gắn `store_code` để chỉ thấy dữ liệu cửa hàng mình + dữ liệu toàn hệ thống.
Thêm pack mới = thêm 1 DomainPack vào PACKS (và builder graph nếu cần).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MemberAttribute:
    key: str
    label: str
    description: str
    scope_filter: bool = False  # True: giá trị thuộc tính giới hạn dữ liệu thành viên được thấy (so với cột store_code của record)


@dataclass(frozen=True)
class ReviewPolicy:
    enabled: bool = False
    keywords: tuple[str, ...] = ()
    require_review_roles: tuple[str, ...] = ("viewer",)
    reason: str = ""


@dataclass(frozen=True)
class DomainPack:
    key: str
    name: str
    description: str
    rule_graph: bool = False  # graph rule-based trên AGE (graph/build.py)
    review: ReviewPolicy = field(default_factory=ReviewPolicy)
    member_attributes: tuple[MemberAttribute, ...] = ()
    doc_types: tuple[str, ...] = ("policy", "report", "contract", "manual", "data", "presentation", "correspondence", "other")
    suggested_questions: tuple[str, ...] = ()

    @property
    def scope_attribute(self) -> MemberAttribute | None:
        return next((a for a in self.member_attributes if a.scope_filter), None)


GENERIC = DomainPack(
    key="generic",
    name="Tổng quát",
    description="Khai phá tài liệu bất kỳ: hỏi đáp, tóm tắt, phân tích bảng số liệu, đồ thị quan hệ, báo cáo.",
    suggested_questions=(
        "Tóm tắt các tài liệu trong workspace này",
        "Những thực thể nào được nhắc tới nhiều nhất?",
        "Có bảng số liệu nào về doanh thu/chi phí không? Vẽ biểu đồ theo thời gian",
    ),
)

RETAIL = DomainPack(
    key="retail",
    name="Bán lẻ",
    description="Chuỗi cửa hàng: bảng giá, tồn kho, khuyến mãi, chính sách, nhà cung cấp; graph SKU-ngành hàng-cửa hàng; duyệt câu hỏi liên quan tiền.",
    rule_graph=True,
    review=ReviewPolicy(
        enabled=True,
        keywords=("giá", "bao nhiêu tiền", "chiết khấu", "giảm giá", "hoàn tiền", "trả lại tiền", "bồi thường", "đền bù",
                  "khấu trừ", "phí", "chi phí", "thanh toán", "voucher", "mức giảm", "ưu đãi bao nhiêu", "vnđ", "vnd", "đồng"),
        require_review_roles=("viewer",),
        reason="Câu hỏi liên quan tiền (giá/chiết khấu/hoàn tiền/bồi thường) cần supervisor duyệt trước khi gửi nhân viên",
    ),
    member_attributes=(MemberAttribute("store_code", "Mã cửa hàng", "Thành viên chỉ thấy dữ liệu cửa hàng này + dữ liệu toàn hệ thống", scope_filter=True),),
    doc_types=("policy", "price_list", "promotion", "catalog", "training", "contract", "inventory", "supplier", "unknown"),
    suggested_questions=(
        "Thời hạn đổi trả hàng điện tử là bao lâu?",
        "Loa ĐT-018 đang có khuyến mãi nào?",
        "Những sản phẩm nào sắp hết hàng ở cửa hàng mình?",
    ),
)

PACKS: dict[str, DomainPack] = {p.key: p for p in (GENERIC, RETAIL)}


def get_pack(key: str | None) -> DomainPack:
    return PACKS.get(key or "generic", GENERIC)


def list_packs() -> list[DomainPack]:
    return list(PACKS.values())

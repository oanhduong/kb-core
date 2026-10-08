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

from core.i18n import L


@dataclass(frozen=True)
class MemberAttribute:
    key: str
    label_vi: str
    description_vi: str
    scope_filter: bool = False  # True: giá trị thuộc tính giới hạn dữ liệu thành viên được thấy (so với cột store_code của record)
    label_en: str = ""
    description_en: str = ""

    @property
    def label(self) -> str:
        return L(self.label_vi, self.label_en or self.label_vi)

    @property
    def description(self) -> str:
        return L(self.description_vi, self.description_en or self.description_vi)


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
    suggested_vi: tuple[str, ...] = ()
    suggested_en: tuple[str, ...] = ()
    # kiến thức nghiệp vụ nối vào system prompt của trợ lý (cách dữ liệu lĩnh vực này liên kết với nhau)
    assistant_hints: tuple[str, ...] = ()

    @property
    def suggested_questions(self) -> tuple[str, ...]:
        """Câu hỏi gợi ý theo ngôn ngữ giao diện của người đang gọi."""
        return L(self.suggested_vi, self.suggested_en or self.suggested_vi)  # type: ignore[arg-type]

    @property
    def scope_attribute(self) -> MemberAttribute | None:
        return next((a for a in self.member_attributes if a.scope_filter), None)


GENERIC = DomainPack(
    key="generic",
    name="Tổng quát",
    description="Khai phá tài liệu bất kỳ: hỏi đáp, tóm tắt, phân tích bảng số liệu, đồ thị quan hệ, báo cáo.",
    suggested_vi=(
        "Tóm tắt các tài liệu trong workspace này",
        "Những thực thể nào được nhắc tới nhiều nhất?",
        "Có bảng số liệu nào về doanh thu/chi phí không? Vẽ biểu đồ theo thời gian",
    ),
    suggested_en=(
        "Summarise the documents in this workspace",
        "Which people, places and things come up most often?",
        "Is there a revenue or cost table? Chart it over time",
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
    member_attributes=(MemberAttribute("store_code", "Mã cửa hàng", "Thành viên chỉ thấy dữ liệu cửa hàng này + dữ liệu toàn hệ thống", scope_filter=True,
                                       label_en="Store code", description_en="The member only sees this store's data plus company-wide data"),),
    doc_types=("policy", "price_list", "promotion", "catalog", "training", "contract", "inventory", "supplier", "unknown"),
    assistant_hints=(
        "Chương trình khuyến mãi áp dụng theo NGÀNH HÀNG (hoặc toàn ngành hàng) và khoảng ngày, hiếm khi theo từng SKU: "
        "khi hỏi khuyến mãi/ưu đãi của 1 sản phẩm, tra SKU để biết ngành hàng, rồi tìm lịch khuyến mãi của ngành hàng đó "
        "và của 'toàn ngành hàng', so ngày áp dụng với hôm nay (đang chạy / sắp tới / đã kết thúc).",
    ),
    suggested_vi=(
        "Thời hạn đổi trả hàng điện tử là bao lâu?",
        "Loa ĐT-018 đang có khuyến mãi nào?",
        "Những sản phẩm nào sắp hết hàng ở cửa hàng mình?",
    ),
    suggested_en=(
        "How long is the return period for electronics?",
        "Which promotion is running for speaker ĐT-018?",
        "Which products are running low at my store?",
    ),
)

PACKS: dict[str, DomainPack] = {p.key: p for p in (GENERIC, RETAIL)}


def get_pack(key: str | None) -> DomainPack:
    return PACKS.get(key or "generic", GENERIC)


def list_packs() -> list[DomainPack]:
    return list(PACKS.values())

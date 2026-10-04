"""Schema CỐ ĐỊNH của metadata graph — nguồn sự thật duy nhất cho build (rule), extract (LLM, A2),
query (A4). LLM không bao giờ được sinh loại node/edge ngoài danh sách này (validate bằng pydantic
ở graph/extract.py dựa trên chính các hằng số dưới đây)."""
from __future__ import annotations

NODE_LABELS: tuple[str, ...] = ("SKU", "Category", "Store", "Promotion", "Policy", "Supplier")

# edge type -> tập (from_label, to_label) hợp lệ
EDGE_TYPES: dict[str, frozenset[tuple[str, str]]] = {
    "BELONGS_TO": frozenset({("SKU", "Category")}),
    "APPLIES_TO": frozenset({("Promotion", "SKU"), ("Promotion", "Category")}),
    "GOVERNS": frozenset({("Policy", "Category")}),
    "REFERENCES": frozenset({("Policy", "Policy")}),
    "STOCKS": frozenset({("Store", "SKU")}),
    "SUPPLIES": frozenset({("Supplier", "SKU")}),
    "HAS_EXCEPTION_FOR": frozenset({("Policy", "Promotion")}),
    # Cấu trúc: Điều (Policy cấp article) thuộc văn bản chính sách (Policy cấp document). Chỉ build
    # bằng rule từ cấu trúc docx — KHÔNG mở cho LLM extraction (xem LLM_EDGE_TYPES).
    "PART_OF": frozenset({("Policy", "Policy")}),
}

LLM_EDGE_TYPES: tuple[str, ...] = (
    "BELONGS_TO", "APPLIES_TO", "GOVERNS", "REFERENCES", "STOCKS", "SUPPLIES", "HAS_EXCEPTION_FOR",
)

# Mô tả tiếng Việt cho decision trace / explanation đường đi graph.
EDGE_LABELS_VI: dict[str, str] = {
    "BELONGS_TO": "thuộc ngành hàng",
    "APPLIES_TO": "áp dụng cho",
    "GOVERNS": "điều chỉnh ngành hàng",
    "REFERENCES": "tham chiếu",
    "STOCKS": "có tồn kho",
    "SUPPLIES": "cung cấp",
    "HAS_EXCEPTION_FOR": "có ngoại lệ cho",
    "PART_OF": "thuộc văn bản",
}

NODE_LABELS_VI: dict[str, str] = {
    "SKU": "Sản phẩm", "Category": "Ngành hàng", "Store": "Cửa hàng", "Promotion": "Khuyến mãi",
    "Policy": "Chính sách", "Supplier": "Nhà cung cấp",
}


def is_valid_edge(edge_type: str, from_label: str, to_label: str) -> bool:
    return (from_label, to_label) in EDGE_TYPES.get(edge_type, frozenset())


def node_display(label: str, props: dict) -> str:
    """Tên người đọc được của 1 node cho explanation (không lộ id kỹ thuật nếu có tên)."""
    node_id = props.get("id", "")
    if label == "SKU":
        name = props.get("name")
        return f"SKU {node_id}" + (f" ({name})" if name else "")
    if label == "Policy":
        return props.get("display") or props.get("title") or str(node_id)
    if label == "Store":
        dn = props.get("display_name")
        return f"cửa hàng {node_id}" + (f" ({dn})" if dn else "")
    if label == "Supplier":
        return f"NCC {props.get('name') or node_id}"
    if label == "Promotion":
        return f"khuyến mãi \"{node_id}\""
    if label == "Category":
        return f"ngành hàng {node_id}"
    return str(node_id)

"""Domain pack: phần nghiệp vụ đặc thù (schema graph rule-based, luật duyệt câu trả lời, thuộc tính
thành viên, gợi ý câu hỏi) tách khỏi lõi tổng quát. Workspace chọn pack qua `workspaces.domain_pack`."""
from domains.packs import DomainPack, get_pack, list_packs

__all__ = ["DomainPack", "get_pack", "list_packs"]

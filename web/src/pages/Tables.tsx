// /tables và /tables/:tableId: cùng trang "Khám phá số liệu", mở sẵn chế độ xem bảng với bảng được chọn.
import { useNavigate, useParams } from "react-router-dom";
import { useWorkspace } from "@/state/auth";
import { ExploreView } from "./Analytics";

export function TablesPage() {
  const { wsId } = useWorkspace();
  const { tableId } = useParams();
  const nav = useNavigate();
  return <ExploreView initialMode="browse" tableId={tableId ?? null} onSelectTable={(id) => nav(`/w/${wsId}/tables/${id}`)} />;
}

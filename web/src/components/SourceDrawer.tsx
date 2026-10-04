// Drawer hiện nguồn trích dẫn: chunk + ngữ cảnh cha + record, link sang tài liệu.
import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { fmtDate, SOURCE_ICON } from "@/lib/format";
import type { Citation } from "@/lib/types";
import { useWorkspace } from "@/state/auth";
import { Drawer, ErrorBox, Loading, StatusBadge } from "./ui";

export function SourceDrawer({ citation, onClose }: { citation: Citation | null; onClose: () => void }) {
  const { wsId } = useWorkspace();
  const q = useQuery({
    queryKey: ["chunk", wsId, citation?.chunk_id],
    queryFn: () => api.chunk(wsId, citation!.chunk_id!),
    enabled: !!citation?.chunk_id,
  });
  return (
    <Drawer open={!!citation} onClose={onClose} title={citation ? `[${citation.n}] ${citation.label}` : ""}>
      {citation && !citation.chunk_id && (
        <div className="space-y-3 text-sm">
          <div className="text-slate-500">Nguồn là kết quả tính toán/bảng số liệu, không có đoạn văn bản gốc.</div>
          {citation.snippet && <pre className="whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs">{citation.snippet}</pre>}
        </div>
      )}
      {citation?.chunk_id && q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && (
        <div className="space-y-4 text-sm">
          {q.data.document && (
            <div className="card flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="truncate font-medium text-slate-900">{SOURCE_ICON[q.data.document.source_type] || "📄"} {q.data.document.title}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <span>{q.data.document.doc_type}</span>
                  {q.data.location && <span>· {q.data.location}</span>}
                  {q.data.effective_date && <span>· hiệu lực {fmtDate(q.data.effective_date)}</span>}
                  <StatusBadge status={q.data.document.status} />
                </div>
              </div>
              <Link to={`/w/${wsId}/documents/${q.data.document.document_id}`} className="btn-secondary btn-sm shrink-0" onClick={onClose}>
                Mở tài liệu <ExternalLink className="h-3 w-3" />
              </Link>
            </div>
          )}
          <section>
            <div className="label">Đoạn được trích</div>
            <div className="whitespace-pre-wrap rounded-lg border border-brand-200 bg-brand-50/50 p-3 leading-6 text-slate-800">{q.data.text}</div>
          </section>
          {q.data.parent_text && q.data.parent_text !== q.data.text && (
            <section>
              <div className="label">Ngữ cảnh rộng hơn</div>
              <div className="max-h-72 overflow-y-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-[13px] leading-6 text-slate-600">{q.data.parent_text}</div>
            </section>
          )}
        </div>
      )}
    </Drawer>
  );
}

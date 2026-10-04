// Danh sách báo cáo + tạo mới (từ prompt -> AI sinh nền, hoặc báo cáo trống để ghim).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileBarChart2, Plus, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Confirm, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Spinner, StatusBadge, useToast } from "@/components/ui";
import { Page } from "@/layout/AppShell";
import { api } from "@/lib/api";
import { fmtRelative, truncate } from "@/lib/format";
import { useWorkspace } from "@/state/auth";

const EXAMPLES = [
  "Báo cáo tổng quan toàn bộ dữ liệu trong workspace: nội dung chính, số liệu nổi bật, các mối liên hệ quan trọng",
  "Tổng hợp các chính sách/quy định hiện hành và điểm cần lưu ý, kèm bảng so sánh nếu có",
  "Phân tích số liệu: xu hướng, top/bottom, phân bố theo nhóm — có biểu đồ cho từng mục",
];

export function ReportsPage() {
  const { wsId, can } = useWorkspace();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [del, setDel] = useState<string | null>(null);
  const reports = useQuery({ queryKey: ["reports", wsId], queryFn: () => api.reports(wsId), refetchInterval: (q) => (q.state.data?.some((r) => r.status === "generating") ? 4000 : false) });
  const delMut = useMutation({ mutationFn: (id: string) => api.deleteReport(wsId, id), onSuccess: () => { setDel(null); void qc.invalidateQueries({ queryKey: ["reports", wsId] }); }, onError: toast.err });
  return (
    <Page>
      <PageHeader title="Báo cáo" subtitle="AI lập dàn ý và viết từng mục bằng dữ liệu thật (có trích dẫn, biểu đồ); bạn chỉnh sửa, ghim thêm kết quả từ chat và xuất PDF/Word/Excel."
        actions={can("editor") && <button className="btn-primary" onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> Tạo báo cáo</button>} />
      {reports.isLoading && <Loading />}
      {reports.error && <ErrorBox error={reports.error} retry={() => reports.refetch()} />}
      {reports.data && reports.data.length === 0 && <Empty icon={<FileBarChart2 className="h-10 w-10" />} title="Chưa có báo cáo" hint="Mô tả báo cáo bạn cần; hệ thống sẽ tìm trong tài liệu, chạy truy vấn số liệu và soạn báo cáo có nguồn."
        action={can("editor") && <button className="btn-primary" onClick={() => setOpen(true)}><Sparkles className="h-4 w-4" /> Tạo báo cáo đầu tiên</button>} />}
      {reports.data && reports.data.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2">
          {reports.data.map((r) => (
            <div key={r.id} className="card group relative p-4 hover:border-brand-300">
              <Link to={r.id} className="block">
                <div className="flex items-start justify-between gap-2"><h3 className="font-semibold text-slate-900">{r.title}</h3><StatusBadge status={r.status} /></div>
                <p className="mt-1 text-sm text-slate-500">{r.description || (r.prompt ? truncate(r.prompt, 140) : "Báo cáo ghim thủ công")}</p>
                <div className="mt-3 text-xs text-slate-400">{r.block_count} block · cập nhật {fmtRelative(r.updated_at)}</div>
              </Link>
              {can("editor") && <button className="btn-ghost btn-sm invisible absolute bottom-3 right-3 group-hover:visible" onClick={() => setDel(r.id)}><Trash2 className="h-3.5 w-3.5" /></button>}
            </div>
          ))}
        </div>
      )}
      <CreateModal open={open} onClose={() => setOpen(false)} onCreated={(id) => nav(`/w/${wsId}/reports/${id}`)} />
      <Confirm open={!!del} onClose={() => setDel(null)} onConfirm={() => del && delMut.mutate(del)} danger title="Xoá báo cáo" message="Báo cáo sẽ bị xoá khỏi danh sách." busy={delMut.isPending} />
    </Page>
  );
}

function CreateModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const { wsId } = useWorkspace();
  const qc = useQueryClient();
  const toast = useToast();
  const [mode, setMode] = useState<"ai" | "blank">("ai");
  const [prompt, setPrompt] = useState("");
  const [title, setTitle] = useState("");
  const mut = useMutation({
    mutationFn: () => api.createReport(wsId, mode === "ai" ? { prompt: prompt.trim(), title: title.trim() || undefined } : { title: title.trim() }),
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["reports", wsId] }); toast.ok(mode === "ai" ? "Đang sinh báo cáo (chạy nền)" : "Đã tạo báo cáo"); onClose(); onCreated(r.id); },
    onError: toast.err,
  });
  return (
    <Modal open={open} onClose={onClose} title="Tạo báo cáo" width="max-w-2xl"
      footer={<><button className="btn-secondary" onClick={onClose}>Huỷ</button><button className="btn-primary" disabled={mut.isPending || (mode === "ai" ? !prompt.trim() : !title.trim())} onClick={() => mut.mutate()}>{mut.isPending && <Spinner className="text-white" />} {mode === "ai" ? "Sinh báo cáo" : "Tạo"}</button></>}>
      <div className="mb-4 flex rounded-lg border border-slate-300 p-0.5 text-sm">
        <button className={`flex-1 rounded-md px-3 py-1.5 ${mode === "ai" ? "bg-brand-600 text-white" : "text-slate-600"}`} onClick={() => setMode("ai")}><Sparkles className="mr-1 inline h-4 w-4" /> AI soạn từ dữ liệu</button>
        <button className={`flex-1 rounded-md px-3 py-1.5 ${mode === "blank" ? "bg-brand-600 text-white" : "text-slate-600"}`} onClick={() => setMode("blank")}>Báo cáo trống (ghim thủ công)</button>
      </div>
      {mode === "ai" ? (
        <div className="space-y-3">
          <Field label="Bạn cần báo cáo gì?" hint="Càng cụ thể (phạm vi, chỉ số, cách nhóm, mục đích) kết quả càng sát. Mỗi mục được viết từ dữ liệu thật và có trích dẫn.">
            <textarea className="input" rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder={EXAMPLES[0]} />
          </Field>
          <div className="flex flex-wrap gap-1.5">{EXAMPLES.map((x) => <button key={x} className="badge bg-slate-100 text-slate-600 hover:bg-brand-50 hover:text-brand-700" onClick={() => setPrompt(x)}>{truncate(x, 60)}</button>)}</div>
          <Field label="Tiêu đề (tuỳ chọn)"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Để trống: AI tự đặt" /></Field>
        </div>
      ) : (
        <Field label="Tiêu đề"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Ví dụ: Tổng hợp tuần 40" /></Field>
      )}
    </Modal>
  );
}

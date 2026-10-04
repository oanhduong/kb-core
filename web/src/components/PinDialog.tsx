// Ghim 1 câu trả lời (trace) vào báo cáo có sẵn hoặc báo cáo mới.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { useWorkspace } from "@/state/auth";
import { Field, Modal, Spinner, useToast } from "./ui";

export function PinDialog({ traceId, question, onClose }: { traceId: string | null; question?: string; onClose: () => void }) {
  const { wsId } = useWorkspace();
  const toast = useToast();
  const qc = useQueryClient();
  const nav = useNavigate();
  const reports = useQuery({ queryKey: ["reports", wsId], queryFn: () => api.reports(wsId), enabled: !!traceId });
  const [target, setTarget] = useState<string>("__new__");
  const [newTitle, setNewTitle] = useState("Báo cáo mới");
  const [heading, setHeading] = useState(question ? question.slice(0, 120) : "");
  const [withAnswer, setWithAnswer] = useState(true);
  const [withArtifacts, setWithArtifacts] = useState(true);
  const editable = (reports.data || []).filter((r) => r.status !== "generating");
  const mut = useMutation({
    mutationFn: async () => {
      let id = target;
      if (id === "__new__") id = (await api.createReport(wsId, { title: newTitle.trim() || "Báo cáo mới" })).id;
      await api.pinToReport(wsId, id, { trace_id: traceId!, heading: heading.trim() || undefined, include_answer: withAnswer, include_artifacts: withArtifacts });
      return id;
    },
    onSuccess: (id) => {
      void qc.invalidateQueries({ queryKey: ["reports", wsId] });
      void qc.invalidateQueries({ queryKey: ["report", wsId, id] });
      toast.ok("Đã ghim vào báo cáo");
      onClose();
      nav(`/w/${wsId}/reports/${id}`);
    },
    onError: toast.err,
  });
  return (
    <Modal open={!!traceId} onClose={onClose} title="Ghim vào báo cáo"
      footer={<><button className="btn-secondary" onClick={onClose}>Huỷ</button><button className="btn-primary" disabled={mut.isPending} onClick={() => mut.mutate()}>{mut.isPending && <Spinner className="text-white" />} Ghim</button></>}>
      <div className="space-y-3">
        <Field label="Báo cáo">
          <select className="input" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="__new__">+ Tạo báo cáo mới</option>
            {editable.map((r) => <option key={r.id} value={r.id}>{r.title} ({r.block_count} block)</option>)}
          </select>
        </Field>
        {target === "__new__" && <Field label="Tên báo cáo mới"><input className="input" value={newTitle} onChange={(e) => setNewTitle(e.target.value)} /></Field>}
        <Field label="Tiêu đề mục (tuỳ chọn)"><input className="input" value={heading} onChange={(e) => setHeading(e.target.value)} placeholder="Ví dụ: Cơ cấu SKU theo ngành" /></Field>
        <div className="flex gap-4 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" checked={withAnswer} onChange={(e) => setWithAnswer(e.target.checked)} /> Nội dung trả lời</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={withArtifacts} onChange={(e) => setWithArtifacts(e.target.checked)} /> Biểu đồ / bảng số liệu</label>
        </div>
      </div>
    </Modal>
  );
}

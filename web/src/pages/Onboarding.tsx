// Tạo workspace (lần đầu hoặc thêm mới): chọn domain pack.
import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, Sparkles } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, auth } from "@/lib/api";
import { ErrorBox, Field, Spinner } from "@/components/ui";
import { useAuth } from "@/state/auth";

export function OnboardingPage() {
  const { workspaces, refresh, logout } = useAuth();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const packs = useQuery({ queryKey: ["domain-packs"], queryFn: api.domainPacks });
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [pack, setPack] = useState("generic");
  const mut = useMutation({
    mutationFn: () => api.createWorkspace({ name: name.trim(), description: desc.trim() || undefined, domain_pack: pack }),
    onSuccess: async (w) => {
      auth.setWorkspace(w.id);
      await refresh();
      nav(`/w/${w.id}`);
    },
  });
  const isNew = params.get("new") === "1" && workspaces.length > 0;
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-brand-50 via-slate-50 to-slate-100 p-4">
      <div className="w-full max-w-xl">
        <div className="mb-6 flex items-center justify-center gap-2 text-brand-700"><Sparkles className="h-6 w-6" /><span className="text-lg font-semibold">KB Explorer</span></div>
        <div className="card p-6">
          {isNew ? (
            <Link to="/" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="h-4 w-4" /> Quay lại</Link>
          ) : null}
          <h1 className="text-lg font-semibold text-slate-900">{isNew ? "Tạo workspace mới" : "Tạo workspace đầu tiên"}</h1>
          <p className="mb-5 mt-1 text-sm text-slate-500">Workspace là nơi chứa tài liệu, bảng số liệu, đồ thị tri thức và báo cáo. Bạn có thể mời đồng nghiệp vào sau.</p>
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); mut.mutate(); }}>
            <Field label="Tên workspace"><input className="input" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} placeholder="Ví dụ: Dữ liệu dự án Q4" /></Field>
            <Field label="Mô tả (tuỳ chọn)"><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
            <div>
              <div className="label">Loại dữ liệu</div>
              <div className="grid gap-2 sm:grid-cols-2">
                {(packs.data || []).map((p) => (
                  <button type="button" key={p.key} onClick={() => setPack(p.key)}
                    className={clsx("rounded-lg border p-3 text-left transition", pack === p.key ? "border-brand-500 bg-brand-50 ring-2 ring-brand-500/20" : "border-slate-200 hover:bg-slate-50")}>
                    <div className="text-sm font-semibold text-slate-900">{p.name}</div>
                    <div className="mt-0.5 text-xs text-slate-500">{p.description}</div>
                  </button>
                ))}
              </div>
            </div>
            {mut.error ? <ErrorBox error={mut.error} /> : null}
            <button className="btn-primary w-full" disabled={mut.isPending || !name.trim()}>{mut.isPending && <Spinner className="text-white" />} Tạo workspace</button>
          </form>
          {!isNew && <button className="mt-4 w-full text-center text-sm text-slate-500 hover:text-slate-800" onClick={() => { logout(); nav("/login"); }}>Đăng xuất</button>}
        </div>
      </div>
    </div>
  );
}

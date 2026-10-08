// Tạo workspace (lần đầu hoặc thêm mới): tên, mô tả, loại công việc (domain pack) giải thích bằng lời thường.
// PackChooser dùng lại ở Cài đặt > Chung.
import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, Check } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ErrorBox, Field, Loading, Spinner } from "@/components/ui";
import { useT } from "@/i18n";
import { api, auth } from "@/lib/api";
import type { DomainPack } from "@/lib/types";
import { AuthShell } from "@/pages/Auth";
import { useAuth } from "@/state/auth";

type T = ReturnType<typeof useT>;
/** Chữ của pack theo ngôn ngữ; pack lạ (chưa có trong từ điển) -> dùng chữ API trả về. */
function packText(t: T, p: DomainPack, part: "name" | "desc"): string {
  const k = `auth.pack.${p.key}.${part}`;
  const out = t(k);
  return out === k ? (part === "name" ? p.name : p.description) : out;
}
function packExtra(t: T, p: DomainPack): string | null {
  const k = `auth.pack.${p.key}.extra`;
  const out = t(k);
  return out === k ? null : out;
}

export function PackChooser({ packs, value, onChange, disabled }: { packs: DomainPack[]; value: string; onChange: (k: string) => void; disabled?: boolean }) {
  const t = useT();
  return (
    <div role="radiogroup" aria-label={t("auth.onboarding.pack")} className="grid gap-2.5 sm:grid-cols-2">
      {packs.map((p) => {
        const on = value === p.key;
        const extra = packExtra(t, p);
        return (
          <button type="button" role="radio" aria-checked={on} key={p.key} disabled={disabled} onClick={() => onChange(p.key)}
            className={clsx("relative rounded-xl border bg-white p-4 text-left transition disabled:cursor-not-allowed",
              on ? "border-brand-500 shadow-focus" : "border-ink-100 hover:border-ink-300", disabled && !on && "opacity-60")}>
            <span className={clsx("absolute right-3 top-3 flex h-5 w-5 items-center justify-center rounded-full border", on ? "border-brand-600 bg-brand-600 text-white" : "border-ink-300")}>
              {on && <Check className="h-3 w-3" />}
            </span>
            <span className="block pr-7 font-display text-[15px] font-semibold text-ink-900">{packText(t, p, "name")}</span>
            <span className="mt-1 block text-xs leading-5 text-ink-600">{packText(t, p, "desc")}</span>
            {extra && <span className="mt-2 block text-xs leading-5 text-ink-800">{extra}</span>}
          </button>
        );
      })}
    </div>
  );
}

export function OnboardingPage() {
  const { workspaces, refresh, logout } = useAuth();
  const t = useT();
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
    <AuthShell wide
      title={isNew ? t("auth.onboarding.titleNew") : t("auth.onboarding.titleFirst")}
      subtitle={t("auth.onboarding.subtitle")}
      top={isNew ? <Link to="/" className="mb-5 inline-flex items-center gap-1.5 rounded-lg text-sm text-ink-600 hover:text-ink-900"><ArrowLeft className="h-4 w-4" /> {t("action.back")}</Link> : null}>
      <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); if (name.trim()) mut.mutate(); }}>
        <Field label={t("auth.onboarding.name")}><input className="input py-2.5" required autoFocus maxLength={120} value={name} onChange={(e) => setName(e.target.value)} placeholder={t("auth.onboarding.namePlaceholder")} /></Field>
        <Field label={t("auth.onboarding.description")} hint={t("auth.onboarding.descriptionHint")}><input className="input py-2.5" value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
        <div>
          <span className="label">{t("auth.onboarding.pack")}</span>
          <p className="mb-3 text-xs leading-5 text-ink-600">{t("auth.onboarding.packHint")}</p>
          {packs.isLoading ? <Loading /> : packs.error ? <ErrorBox error={packs.error} retry={() => packs.refetch()} /> : (
            <PackChooser packs={packs.data || []} value={pack} onChange={setPack} />
          )}
        </div>
        {mut.error ? <ErrorBox error={mut.error} /> : null}
        <button className="btn-primary w-full py-2.5" disabled={mut.isPending || !name.trim()}>{mut.isPending && <Spinner className="text-white" />} {t("auth.onboarding.submit")}</button>
      </form>
      {!isNew && <button className="btn-ghost mt-4 w-full" onClick={() => { logout(); nav("/login"); }}>{t("menu.signOut")}</button>}
    </AuthShell>
  );
}

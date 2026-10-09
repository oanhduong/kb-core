// Song ngữ vi/en. Mỗi khu vực UI có 1 file từ điển riêng (i18n/<khu-vực>.ts: { en: {...}, vi: {...} }) -> gộp ở đây.
// t("key", {n: 3}) thay {n}; thiếu key ở ngôn ngữ hiện tại -> lấy ngôn ngữ còn lại -> trả chính key (dễ phát hiện).
// Ngôn ngữ lưu localStorage; mặc định theo trình duyệt. Chữ từ API dạng {en, vi} dùng pick().
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import common from "./common";
import home from "./home";
import chat from "./chat";
import sources from "./sources";
import models from "./models";
import explore from "./explore";
import reports from "./reports";
import graph from "./graph";
import trust from "./trust";
import settings from "./settings";
import auth from "./auth";
import about from "./about";
import tech from "./tech";

export type Lang = "vi" | "en";
type Dict = Record<string, string>;
type Module = { en: Dict; vi: Dict };

const MODULES: Module[] = [common, home, chat, sources, models, explore, reports, graph, trust, settings, auth, about, tech];
const DICTS: Record<Lang, Dict> = { en: {}, vi: {} };
for (const m of MODULES) {
  Object.assign(DICTS.en, m.en);
  Object.assign(DICTS.vi, m.vi);
}

const KEY = "kbx.lang";
let current: Lang = detect();

function detect(): Lang {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "vi" || saved === "en") return saved;
  } catch { /* storage bị chặn */ }
  return typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("vi") ? "vi" : "en";
}

export function getLang(): Lang {
  return current;
}

export function translate(key: string, vars?: Record<string, string | number | null | undefined>, lang: Lang = current): string {
  const raw = DICTS[lang][key] ?? DICTS[lang === "vi" ? "en" : "vi"][key] ?? key;
  if (!vars) return raw;
  return raw.replace(/\{(\w+)\}/g, (m, k) => (vars[k] === undefined || vars[k] === null ? m : String(vars[k])));
}

/** Chọn chữ theo ngôn ngữ từ object {en, vi} của API. */
export function pick(v: { en?: string; vi?: string } | string | null | undefined, lang: Lang = current): string {
  if (!v) return "";
  if (typeof v === "string") return v;
  return (lang === "vi" ? v.vi || v.en : v.en || v.vi) || "";
}

interface I18n {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: typeof translate;
  pick: typeof pick;
}

const Ctx = createContext<I18n | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(current);
  const setLang = useCallback((l: Lang) => {
    current = l;
    try { localStorage.setItem(KEY, l); } catch { /* bỏ qua */ }
    setLangState(l);
  }, []);
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  const value = useMemo<I18n>(() => ({
    lang, setLang,
    t: (k, v, l) => translate(k, v, l ?? lang),
    pick: (v, l) => pick(v, l ?? lang),
  }), [lang, setLang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18n {
  const v = useContext(Ctx);
  if (!v) throw new Error("useI18n ngoài I18nProvider");
  return v;
}

export function useT() {
  return useI18n().t;
}

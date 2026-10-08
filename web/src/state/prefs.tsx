// Tuỳ chọn hiển thị của người dùng (localStorage): "chế độ kỹ thuật" bật thì hiện SQL, mã định danh, dữ kiện thô,
// việc nền, chi phí… Mặc định tắt — người dùng nghiệp vụ chỉ thấy những gì cần để ra quyết định.
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

const KEY = "kbx.advanced";
interface Prefs { advanced: boolean; setAdvanced: (v: boolean) => void }
const Ctx = createContext<Prefs | null>(null);

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [advanced, set] = useState<boolean>(() => {
    try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
  });
  const setAdvanced = useCallback((v: boolean) => {
    set(v);
    try { localStorage.setItem(KEY, v ? "1" : "0"); } catch { /* bỏ qua */ }
  }, []);
  const value = useMemo(() => ({ advanced, setAdvanced }), [advanced, setAdvanced]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePrefs(): Prefs {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePrefs ngoài PrefsProvider");
  return v;
}

/** Chỉ render khi bật chế độ kỹ thuật. */
export function Advanced({ children }: { children: ReactNode }) {
  return usePrefs().advanced ? <>{children}</> : null;
}

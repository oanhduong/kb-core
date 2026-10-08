// Đổi ngôn ngữ -> tải lại mọi dữ liệu đang cache (API trả chữ theo Accept-Language: lỗi, nhãn, nội dung AI đã dịch).
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/state/auth";

export function LangRefetch() {
  const { lang } = useI18n();
  const qc = useQueryClient();
  const { refresh, user } = useAuth();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    void qc.invalidateQueries();
    if (user) void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang]);
  return null;
}

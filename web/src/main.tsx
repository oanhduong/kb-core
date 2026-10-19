import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { ToastProvider } from "./components/ui";
import { I18nProvider } from "./i18n";
import { AuthProvider } from "./state/auth";
import { PrefsProvider } from "./state/prefs";
import { LangRefetch } from "./state/langRefetch";
import "./styles.css";

const qc = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15_000 } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>
      <PrefsProvider>
        <QueryClientProvider client={qc}>
          <ToastProvider>
            <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/+$/, "")}>
              <AuthProvider>
                <LangRefetch />
                <App />
              </AuthProvider>
            </BrowserRouter>
          </ToastProvider>
        </QueryClientProvider>
      </PrefsProvider>
    </I18nProvider>
  </React.StrictMode>,
);

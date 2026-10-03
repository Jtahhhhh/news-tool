import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster, toast } from "sonner";
import { api, post } from "./api/client";
import { Shell } from "./layouts/shell";
import { Overview } from "./pages/overview";
import { News } from "./pages/news";
import { Scripts, ScriptDetail } from "./pages/scripts";
import { Videos, Jobs, Publish, Storage, Settings } from "./pages/operations";
import { Button } from "./components/ui/button";
import "./index.css";
const client = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5000 } },
});
function App() {
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [authRequired, setAuthRequired] = useState(true);
  async function session() {
    try {
      const s = await api<{ authenticated: boolean; required: boolean }>(
        "/api/auth/session",
      );
      setAuthenticated(s.authenticated);
      setAuthRequired(s.required);
      setError("");
    } catch {
      setError("Không kết nối được API. Kiểm tra backend và thử lại.");
    } finally {
      setReady(true);
    }
  }
  useEffect(() => {
    session();
    const expired = () => {
      setAuthenticated(false);
      client.clear();
    };
    window.addEventListener("session-expired", expired);
    return () => window.removeEventListener("session-expired", expired);
  }, []);
  if (!ready)
    return (
      <div className="login">
        <p>Connecting to your workspace…</p>
      </div>
    );
  if (!authenticated)
    return (
      <div className="login">
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              await post("/api/auth/login", { email, password });
              setPassword("");
              await session();
            } catch (e) {
              toast.error((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="brand">newsroom.</div>
          <h1>Welcome back</h1>
          <p>Sign in to your editorial workspace.</p>
          {error && (
            <p role="alert">
              {error}{" "}
              <button type="button" onClick={session}>
                Thử lại
              </button>
            </p>
          )}
          <label>
            Email
            <input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <Button disabled={busy}>Sign in</Button>
        </form>
      </div>
    );
  return (
    <BrowserRouter basename="/dashboard">
      <Routes>
        <Route
          element={
            <Shell
              authRequired={authRequired}
              logout={() => setAuthenticated(false)}
            />
          }
        >
          <Route index element={<Overview />} />
          <Route path="news" element={<News />} />
          <Route path="scripts" element={<Scripts />} />
          <Route path="scripts/:id" element={<ScriptDetail />} />
          <Route path="videos" element={<Videos />} />
          <Route path="publish" element={<Publish />} />
          <Route path="jobs" element={<Jobs />} />
          <Route path="storage" element={<Storage />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <App />
      <Toaster richColors position="bottom-right" />
    </QueryClientProvider>
  </React.StrictMode>,
);

import { NavLink, Outlet, useLocation, Link } from "react-router-dom";
import {
  LayoutDashboard,
  Newspaper,
  FileText,
  Film,
  Send,
  ListTodo,
  HardDrive,
  Settings,
  RefreshCw,
  LogOut,
  ChevronRight,
  Clapperboard,
  ArrowUpRight,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useApi } from "../hooks/use-api";
import { Button } from "../components/ui/button";
import { post } from "../api/client";
const entries = [
  ["", "Dashboard", LayoutDashboard],
  ["news", "News", Newspaper],
  ["scripts", "Scripts", FileText],
  ["videos", "Videos", Film],
  ["publish", "Publish", Send],
  ["jobs", "Jobs", ListTodo],
  ["storage", "Storage", HardDrive],
  ["settings", "Settings", Settings],
] as const;
export function Shell({
  logout,
  authRequired,
}: {
  logout: () => void;
  authRequired: boolean;
}) {
  const client = useQueryClient();
  const location = useLocation();
  const currentPage =
    entries.find(([path]) => path === location.pathname.split("/")[1])?.[1] ||
    "Workspace";
  const health = useApi<{
    environment: string;
    services: Record<string, string>;
  }>("/api/health/services", 30000);
  return (
    <div className="app">
      <aside>
        <div className="brand">
          <span>
            <Clapperboard size={21} />
          </span>
          newsroom<span className="brand-period">.</span>
        </div>
        <div className="workspace">
          <span className="workspace-avatar">N</span>
          <div>
            News to video studio<small>Your creative workspace</small>
          </div>
        </div>
        <div className="nav-caption">WORKSPACE</div>
        <nav>
          {entries.map(([path, label, Icon]) => (
            <NavLink key={path} to={"/" + path} end={path === ""} title={label}>
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="sidebar-tip">
            <Clapperboard size={23} />
            <strong>Great stories start here.</strong>
            <p>
              One workspace.
              <br />
              Endless possibilities.
            </p>
            <Link to="/news">
              Find your next story <ArrowUpRight size={14} />
            </Link>
          </div>
          <div className="sidebar-signature">
            <span className="dot online" />
            NEWSROOM STUDIO <span>v1.0</span>
          </div>
        </div>
      </aside>
      <div className="main">
        <header>
          <div className="breadcrumb">
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{currentPage}</strong>
          </div>
          <div className="header-actions">
            <div className="connection-status">
              <span
                className={
                  "dot " +
                  (health.data?.services.PostgreSQL === "ok" ? "online" : "")
                }
              />
              {health.data?.services.PostgreSQL === "ok"
                ? "System connected"
                : "Checking services"}
            </div>
            <span className="env">{health.data?.environment || "—"}</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => client.invalidateQueries()}
            >
              <RefreshCw size={14} /> Refresh
            </Button>
            {authRequired && (
              <Button
                variant="outline"
                size="sm"
                aria-label="Đăng xuất"
                onClick={async () => {
                  await post("/api/auth/logout");
                  client.clear();
                  logout();
                }}
              >
                <LogOut size={14} />
              </Button>
            )}
            <div className="user-avatar" title="Editorial workspace">
              N
            </div>
          </div>
        </header>
        <main>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

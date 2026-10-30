import { useEffect, useState, useRef } from "react";
import { NavLink } from "react-router-dom";
import {
  Home,
  Database,
  FileText,
  Settings,
  Compass,
  ChevronDown,
  FolderOpen,
  Plus,
  RefreshCw,
} from "lucide-react";
import { clsx } from "clsx";
import { useWorkspaceStore } from "../../stores/workspace";
import { useSettingsStore } from "../../stores/settings";
import { useWorkspaceManager } from "../../stores/workspaceManager";
import { useNavigate } from "react-router-dom";

const navItems = [
  { to: "/work", icon: FileText, label: "Work" },
  { to: "/workspace", icon: Home, label: "Workspace" },
  { to: "/evidence", icon: Database, label: "Evidence" },
];

const statusColor: Record<string, string> = {
  ready: "bg-green-500",
  connecting: "bg-yellow-500 animate-pulse",
  unavailable: "bg-red-500",
};

export default function Sidebar() {
  const engineStatus = useWorkspaceStore((s) => s.engineStatus);
  const setEngineStatus = useWorkspaceStore((s) => s.setEngineStatus);
  const productName = useWorkspaceStore((s) => s.productName);
  const workspacePath = useWorkspaceStore((s) => s.workspacePath);
  const switchWorkspace = useWorkspaceStore((s) => s.switchWorkspace);
  const tokenUsage = useSettingsStore((s) => s.tokenUsage);
  const fetchUsage = useSettingsStore((s) => s.fetchUsage);
  const { workspaces, loadWorkspaces, loaded, openWorkspace } = useWorkspaceManager();
  const navigate = useNavigate();
  const [syncingCount, setSyncingCount] = useState(0);
  const [showPicker, setShowPicker] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!loaded) loadWorkspaces();
  }, [loaded]);

  // Close picker on outside click
  useEffect(() => {
    if (!showPicker) return;
    function handleClick(e: MouseEvent) {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setShowPicker(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [showPicker]);

  useEffect(() => {
    let cancelled = false;
    async function check() {
      try {
        const res = await window.compass?.engine.health();
        if (!cancelled) {
          setEngineStatus(res?.status === "ready" ? "ready" : "unavailable");
        }
      } catch {
        if (!cancelled) setEngineStatus("unavailable");
      }
    }
    check();
    const interval = setInterval(check, 10000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [setEngineStatus]);

  useEffect(() => {
    if (engineStatus === "ready") fetchUsage();
    const interval = setInterval(() => {
      if (engineStatus === "ready") fetchUsage();
    }, 30000);
    return () => clearInterval(interval);
  }, [engineStatus, fetchUsage]);

  // Poll sync status
  useEffect(() => {
    if (engineStatus !== "ready" || !workspacePath) return;
    async function checkSync() {
      try {
        const res = await window.compass?.engine.call("/sync/status", { workspace_path: workspacePath }) as { sources?: Array<{ syncing: boolean }> };
        const syncing = res?.sources?.filter((s) => s.syncing).length || 0;
        setSyncingCount(syncing);
      } catch {
        // Sync endpoints may not be available
      }
    }
    checkSync();
    const interval = setInterval(checkSync, 15000);
    return () => clearInterval(interval);
  }, [engineStatus, workspacePath]);

  return (
    <aside className="flex flex-col w-[220px] h-full bg-compass-sidebar border-r border-compass-border select-none">
      {/* Title bar drag region — pl-[78px] clears macOS traffic light buttons */}
      <div className="h-12 flex items-center pl-[78px] pr-3 draggable shrink-0"
           style={{ WebkitAppRegion: "drag" } as React.CSSProperties}>
        <div className="relative flex-1 min-w-0" ref={pickerRef}
             style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}>
          <button
            onClick={() => setShowPicker(!showPicker)}
            className="flex items-center gap-2 w-full text-left hover:bg-white/5 rounded-lg px-1 py-1 transition-colors"
          >
            <Compass className="w-5 h-5 text-compass-accent shrink-0" />
            <span className="text-sm font-semibold text-compass-text tracking-tight truncate flex-1">
              {productName || "Compass"}
            </span>
            <ChevronDown className={clsx("w-3.5 h-3.5 text-compass-muted shrink-0 transition-transform", showPicker && "rotate-180")} />
          </button>

          {showPicker && (
            <div className="absolute top-full left-0 right-0 mt-1 bg-compass-card border border-compass-border rounded-lg shadow-xl z-50 overflow-hidden">
              <div className="max-h-48 overflow-y-auto py-1">
                {workspaces.map((ws) => (
                  <button
                    key={ws.id}
                    onClick={() => {
                      if (ws.path !== workspacePath) {
                        openWorkspace(ws.id);
                        switchWorkspace(ws.path, ws.name, ws.description);
                      }
                      setShowPicker(false);
                    }}
                    className={clsx(
                      "w-full flex items-center gap-2 px-3 py-2 text-left text-sm transition-colors",
                      ws.path === workspacePath
                        ? "bg-compass-accent/10 text-compass-accent"
                        : "text-compass-muted hover:text-compass-text hover:bg-white/5"
                    )}
                  >
                    <FolderOpen className="w-3.5 h-3.5 shrink-0" />
                    <span className="truncate">{ws.name}</span>
                  </button>
                ))}
              </div>
              <button
                onClick={() => {
                  setShowPicker(false);
                  navigate("/workspace");
                  useWorkspaceStore.getState().clearWorkspace();
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-compass-muted hover:text-compass-text hover:bg-white/5 border-t border-compass-border transition-colors"
              >
                <Plus className="w-3.5 h-3.5 shrink-0" />
                New Product
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 flex flex-col gap-0.5 px-2 pt-1 overflow-y-auto">
        {navItems.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              clsx(
                "flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors",
                isActive
                  ? "bg-white/10 text-compass-text"
                  : "text-compass-muted hover:text-compass-text hover:bg-white/5"
              )
            }
          >
            <Icon className="w-4 h-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Bottom section */}
      <div className="px-2 pb-3 shrink-0">
        <NavLink
          to="/settings"
          className={({ isActive }) =>
            clsx(
              "flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors",
              isActive
                ? "bg-white/10 text-compass-text"
                : "text-compass-muted hover:text-compass-text hover:bg-white/5"
            )
          }
        >
          <Settings className="w-4 h-4 shrink-0" />
          Settings
        </NavLink>
        <div className="mt-2 px-3 py-1.5 space-y-1">
          <div className="flex items-center gap-2">
            <div className={clsx("w-2 h-2 rounded-full", statusColor[engineStatus])} />
            <span className="text-xs text-compass-muted">
              Engine {engineStatus}
            </span>
          </div>
          {syncingCount > 0 && (
            <div className="flex items-center gap-1.5 text-xs text-compass-accent">
              <RefreshCw className="w-3 h-3 animate-spin" />
              Syncing {syncingCount} source{syncingCount > 1 ? "s" : ""}
            </div>
          )}
          {tokenUsage.total > 0 && (
            <div className="text-xs text-compass-muted">
              {tokenUsage.total.toLocaleString()} tokens &middot; {tokenUsage.cost}
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}

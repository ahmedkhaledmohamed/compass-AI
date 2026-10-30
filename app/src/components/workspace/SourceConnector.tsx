import { useState } from "react";
import { Code2, FileText, BarChart3, Mic, Headphones, Check, FolderOpen, File } from "lucide-react";
import { clsx } from "clsx";
import { useWorkspaceStore } from "../../stores/workspace";

interface SourceDef {
  type: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
  bgColor: string;
  description: string;
  picker: "directory" | "file";
  /** Whether this source type supports live API mode. */
  supportsLive?: boolean;
}

const SOURCE_DEFS: SourceDef[] = [
  {
    type: "code",
    label: "Code",
    icon: Code2,
    color: "text-compass-code",
    bgColor: "bg-compass-code/10",
    description: "Local code folder",
    picker: "directory",
    supportsLive: true,
  },
  {
    type: "docs",
    label: "Docs",
    icon: FileText,
    color: "text-compass-docs",
    bgColor: "bg-compass-docs/10",
    description: "Strategy docs, PRDs, roadmaps",
    picker: "directory",
    supportsLive: true,
  },
  {
    type: "analytics",
    label: "Analytics",
    icon: BarChart3,
    color: "text-compass-data",
    bgColor: "bg-compass-data/10",
    description: "Usage data (CSV / JSON)",
    picker: "file",
  },
  {
    type: "interviews",
    label: "Interviews",
    icon: Mic,
    color: "text-compass-judgment",
    bgColor: "bg-compass-judgment/10",
    description: "User research transcripts",
    picker: "directory",
  },
  {
    type: "support",
    label: "Support",
    icon: Headphones,
    color: "text-compass-judgment",
    bgColor: "bg-compass-judgment/10",
    description: "Support tickets (CSV)",
    picker: "file",
  },
];

export default function SourceConnector() {
  const workspacePath = useWorkspaceStore((s) => s.workspacePath);
  const sources = useWorkspaceStore((s) => s.sources);
  const addSource = useWorkspaceStore((s) => s.addSource);
  const [connecting, setConnecting] = useState<string | null>(null);

  const connectedTypes = new Set(sources.map((s) => s.type));

  async function handleConnect(def: SourceDef) {
    if (!workspacePath) return;
    setConnecting(def.type);

    try {
      const path =
        def.picker === "directory"
          ? await window.compass.app.selectDirectory()
          : await window.compass.app.selectFile();

      if (!path) {
        setConnecting(null);
        return;
      }

      const res = (await window.compass.engine.call("/connect", {
        workspace_path: workspacePath,
        source_type: def.type,
        name: def.label,
        path,
      })) as { status: string; accessible: boolean };

      if (res.status === "ok") {
        addSource({ type: def.type, name: def.label, path, url: null, options: {} });
      }
    } catch (err) {
      console.error("Connect failed:", err);
    } finally {
      setConnecting(null);
    }
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      {SOURCE_DEFS.map((def) => {
        const Icon = def.icon;
        const connected = connectedTypes.has(def.type);
        const isConnecting = connecting === def.type;

        return (
          <div
            key={def.type}
            className={clsx(
              "p-4 rounded-xl border transition-all text-left",
              connected
                ? "bg-compass-card border-green-500/30"
                : "bg-compass-card border-compass-border"
            )}
          >
            <button
              onClick={() => handleConnect(def)}
              disabled={isConnecting}
              className={clsx(
                "flex items-center gap-3 w-full text-left",
                !connected && "hover:opacity-80"
              )}
            >
              <div className={clsx("p-2 rounded-lg", def.bgColor)}>
                <Icon className={clsx("w-5 h-5", def.color)} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-compass-text">{def.label}</span>
                  {connected && <Check className="w-3.5 h-3.5 text-green-500" />}
                </div>
                <p className="text-xs text-compass-muted truncate">{def.description}</p>
              </div>
              {!connected && (
                <div className="text-compass-muted">
                  {def.picker === "directory" ? (
                    <FolderOpen className="w-4 h-4" />
                  ) : (
                    <File className="w-4 h-4" />
                  )}
                </div>
              )}
            </button>

          </div>
        );
      })}
    </div>
  );
}

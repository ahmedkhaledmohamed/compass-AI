import { useEffect } from "react";
import { Home } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { useWorkspaceStore } from "../stores/workspace";
import { useWorkspaceManager } from "../stores/workspaceManager";
import WorkspaceRecovery from "../components/workspace/WorkspaceRecovery";
import SourceConnector from "../components/workspace/SourceConnector";
import IngestButton from "../components/workspace/IngestButton";
import LegacyArtifacts from "../components/workspace/LegacyArtifacts";
import WorkspacePicker from "../components/workspace/WorkspacePicker";

export default function WorkspacePage() {
  const workspacePath = useWorkspaceStore((s) => s.workspacePath);
  const productName = useWorkspaceStore((s) => s.productName);
  const setWorkspace = useWorkspaceStore((s) => s.setWorkspace);
  const sources = useWorkspaceStore((s) => s.sources);
  const evidenceCount = useWorkspaceStore((s) => s.evidenceCount);
  const evidenceSummary = useWorkspaceStore((s) => s.evidenceSummary);
  const { loaded, loadWorkspaces, openWorkspace } = useWorkspaceManager();
  const navigate = useNavigate();
  const location=useLocation();

  useEffect(() => {
    if (!loaded) loadWorkspaces();
  }, [loaded]);

  useEffect(() => {
    if (loaded && !workspacePath) {
      const mgr = useWorkspaceManager.getState();
      const active = mgr.getActiveWorkspace();
      if (active) {
        setWorkspace(active.path, active.name, active.description);
      }
    }
  }, [loaded]);

  // Load workspace state from engine and auto-ingest if needed
  useEffect(() => {
    if (!workspacePath) return;
    let cancelled=false;
    (async () => {
      try {
        const info = (await window.compass.engine.call("/workspace/info", {
          workspace_path: workspacePath,
        })) as {
          status: string;
          name: string;
          description: string;
          sources: Array<{ id?:string; selection_kind?:"directory"|"file"; approved?:boolean; type: string; name: string; path?: string }>;
          evidence_count: number;
          evidence_summary?:Record<string,number>;
        };
        if (info.status === "ok" && !cancelled) {
          // Populate sources in the store
          const store = useWorkspaceStore.getState();
          store.setSources(
            info.sources.map((s) => ({
              id:s.id,selection_kind:s.selection_kind,approved:s.approved,
              type: s.type,
              name: s.name,
              path: s.path ?? null,
              url: null,
              options: {},
            }))
          );
          // If evidence exists, update the count
          if (info.evidence_count > 0) {
            store.setIngestionResults([], info.evidence_count, info.evidence_summary||{});
          }
        }
      } catch (err) {
        console.error("Failed to load workspace info:", err);
      }
    })();
    return ()=>{cancelled=true;};
  }, [workspacePath]);

  if (!workspacePath) {
    return (
      <WorkspacePicker
        onSelect={async (ws) => {
          try {await openWorkspace(ws.id);} catch {return;}
          setWorkspace(ws.path, ws.name, ws.description);
        }}
        onCreateNew={() => navigate("/onboarding")}
      />
    );
  }

  return (
    <div className="p-8 max-w-4xl">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Home className="w-6 h-6 text-compass-accent" />
          <h1 className="text-2xl font-semibold text-compass-text">{productName}</h1>
        </div>

      </div>

      {location.state?.restoredBackup&&<p role="status" className="mb-6 text-sm text-neutral-300">Backup restored into a new workspace. Source connections were not restored; approve sources before importing again.</p>}
      <div className="grid grid-cols-3 gap-3 mb-8">
        <StatusCard label="Sources" value={sources.length} />
        <StatusCard label="Evidence" value={evidenceCount} />
        <StatusCard
          label="Types"
          value={Object.keys(evidenceSummary).length}
          detail={Object.entries(evidenceSummary)
            .map(([k, v]) => `${k}: ${v}`)
            .join(", ")}
        />
      </div>

      <section className="mb-8">
        <h2 className="text-lg font-medium text-compass-text mb-3">Connect Sources</h2>
        <SourceConnector />
      </section>

      <section className="mb-8">
        <h2 className="text-lg font-medium text-compass-text mb-3">Ingest Evidence</h2>
        <IngestButton />
      </section>

      <LegacyArtifacts workspacePath={workspacePath} />
      <WorkspaceRecovery key={workspacePath} workspace={workspacePath} />
    </div>
  );
}

function StatusCard({ label, value, detail }: { label: string; value: number; detail?: string }) {
  return (
    <div className="rounded-xl bg-compass-card border border-compass-border p-4">
      <p className="text-2xl font-semibold text-compass-text">{value}</p>
      <p className="text-xs text-compass-muted mt-0.5">{label}</p>
      {detail && <p className="text-xs text-compass-muted mt-1 truncate">{detail}</p>}
    </div>
  );
}

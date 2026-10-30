import { Loader2, Play, CheckCircle2 } from "lucide-react";
import { clsx } from "clsx";
import { useWorkspaceStore } from "../../stores/workspace";

export default function IngestButton() {
  const workspacePath = useWorkspaceStore((s) => s.workspacePath);
  const sources = useWorkspaceStore((s) => s.sources);
  const isIngesting = useWorkspaceStore((s) => s.isIngesting);
  const triggerIngestion = useWorkspaceStore((s) => s.triggerIngestion);
  const evidenceCount = useWorkspaceStore((s) => s.evidenceCount);
  const ingestionError=useWorkspaceStore(s=>s.ingestionError);
  const ingestionResults = useWorkspaceStore((s) => s.ingestionResults);

  function handleIngest() {
    if (!workspacePath || sources.length === 0) return;
    triggerIngestion(workspacePath);
  }

  const importedCount=ingestionResults.reduce((sum,result)=>sum+(result.error?0:result.items),0);
  const hasResults = ingestionResults.length > 0;

  return (
    <div className="space-y-3">
      {ingestionError && <p role="alert" className="text-red-400 text-sm">{ingestionError}</p>}
      <button
        onClick={handleIngest}
        disabled={isIngesting || sources.length === 0}
        className={clsx(
          "flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium transition-colors",
          sources.length === 0
            ? "bg-neutral-800 text-neutral-500 cursor-not-allowed"
            : isIngesting
            ? "bg-compass-accent/50 text-white cursor-wait"
            : "bg-compass-accent hover:bg-compass-accent-hover text-white"
        )}
      >
        {isIngesting ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : hasResults ? (
          <CheckCircle2 className="w-4 h-4" />
        ) : (
          <Play className="w-4 h-4" />
        )}
        {isIngesting
          ? `Ingesting ${sources.length} source${sources.length !== 1 ? "s" : ""}...`
          : hasResults
          ? "Re-ingest All Sources"
          : "Ingest All Sources"}
      </button>

      {hasResults && (
        <div className="rounded-lg bg-compass-card border border-compass-border p-4">
          <p className="text-sm font-medium text-compass-text mb-2">
            {importedCount} imported in this run. {evidenceCount} retained evidence items.
          </p>
          <div className="space-y-1">
            {ingestionResults.map((r) => (
              <div key={r.name} className="flex items-center justify-between text-xs">
                <span className="text-compass-muted">{r.name}</span>
                <span className={r.error ? "text-red-400" : "text-compass-text"}>
                  {r.error ? "Error" : `${r.items} items`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

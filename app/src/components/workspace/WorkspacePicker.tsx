import WorkspaceRecovery from "./WorkspaceRecovery";
import {useState} from "react";
import {useWorkspaceStore} from "../../stores/workspace";
import { FolderOpen, Plus, Trash2, Clock } from "lucide-react";
import { clsx } from "clsx";
import { useWorkspaceManager, type WorkspaceEntry } from "../../stores/workspaceManager";

interface Props {
  onSelect: (ws: WorkspaceEntry) => void;
  onCreateNew: () => void;
}

export default function WorkspacePicker({ onSelect, onCreateNew }: Props) {
  const { workspaces, removeWorkspace,pickWorkspace,error } = useWorkspaceManager();

  const [busy,setBusy]=useState(false);
  const [pickerError,setPickerError]=useState("");
  async function handleOpen(){
    setBusy(true);setPickerError("");
    try {const selected=await pickWorkspace();if(selected)useWorkspaceStore.getState().setWorkspace(selected.id,selected.name,selected.description);}
    catch(cause){setPickerError(cause instanceof Error?cause.message:"This folder could not be opened.");}
    finally{setBusy(false);}
  }

  const sorted = [...workspaces].sort(
    (a, b) => new Date(b.last_opened).getTime() - new Date(a.last_opened).getTime()
  );

  return (
    <div className="flex items-center justify-center h-full">
      <div className="w-full max-w-md p-8">
        <h1 className="text-xl font-semibold text-compass-text mb-1">Welcome to Compass</h1>
        <p className="text-sm text-compass-muted mb-6">
          Open an existing product workspace or create a new one.
        </p>

        {sorted.length > 0 && (
          <div className="space-y-2 mb-6">
            {sorted.map((ws) => (
              <div
                key={ws.id}
                className="flex items-center gap-3 p-3 rounded-xl bg-compass-card border border-compass-border hover:border-compass-accent/30 transition-colors group"
              >
                <button
                  onClick={() => onSelect(ws)}
                  className="flex-1 flex items-center gap-3 text-left min-w-0"
                >
                  <FolderOpen className="w-5 h-5 text-compass-accent shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-compass-text truncate">{ws.name}</p>
                    <div className="flex items-center gap-1 text-xs text-compass-muted">
                      <Clock className="w-3 h-3" />
                      {new Date(ws.last_opened).toLocaleDateString()}
                    </div>
                  </div>
                </button>
                <button
                  onClick={async () => {try {await removeWorkspace(ws.id);if(useWorkspaceStore.getState().workspacePath===ws.id)useWorkspaceStore.getState().clearWorkspace();}catch{setPickerError("This workspace registration could not be removed.");}}}
                  className="opacity-0 group-hover:opacity-100 text-compass-muted hover:text-red-400 transition-all"
                  title="Remove"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        )}

        {(error||pickerError) && <p role="alert" className="text-red-400 text-sm my-3">{pickerError||error}</p>}
        <button disabled={busy} onClick={handleOpen} className="w-full border border-compass-border rounded-xl px-4 py-3 mb-3 disabled:opacity-50">{busy?"Opening…":"Open existing workspace"}</button>
        <button
          onClick={onCreateNew}
          className="w-full flex items-center justify-center gap-2 py-3 rounded-xl border border-dashed border-compass-border text-sm text-compass-muted hover:text-compass-text hover:border-compass-accent/30 transition-colors"
        >
          <Plus className="w-4 h-4" />
          Create New Product
        </button>
        <WorkspaceRecovery workspace={null}/>
      </div>
    </div>
  );
}

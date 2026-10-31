import {useRef,useEffect,useState} from "react";
import {useNavigate} from "react-router-dom";
import {useWorkspaceManager} from "../../stores/workspaceManager";
import {useWorkspaceStore} from "../../stores/workspace";

export default function WorkspaceRecovery({workspace,onRestored}:{workspace:string|null;onRestored?:()=>void}){
 const [busy,setBusy]=useState(""),[notice,setNotice]=useState(""),[error,setError]=useState("");
 const epoch=useRef(0),navigate=useNavigate();
 useEffect(()=>{epoch.current++;setNotice("");setError("");return()=>{epoch.current++;};},[workspace]);
 async function backup(){
  if(!workspace)return;const token=epoch.current;setBusy("Backing up workspace…");setNotice("");setError("");
  try{const result=await window.compass.workspaces.backup(workspace);if(epoch.current===token)setNotice(result.status==="saved"?`Backup saved: ${result.tasks} ${result.tasks===1?"task":"tasks"}, ${result.snapshots} evidence ${result.snapshots===1?"snapshot":"snapshots"}, ${result.documents} older ${result.documents===1?"document":"documents"}.`:"Backup cancelled. Existing work was kept.");}
  catch(cause){if(epoch.current===token)setError(cause instanceof Error?cause.message:"Backup failed. Existing work was kept.");}
  finally{if(epoch.current===token)setBusy("");}
 }
 async function restore(){
  const token=epoch.current;setBusy("Restoring into a new workspace…");setNotice("");setError("");
  try{const result=await window.compass.workspaces.restore();if(epoch.current!==token)return;
   if(!result){setNotice("Restore cancelled. Existing work was kept.");return;}
   await useWorkspaceManager.getState().loadWorkspaces();
   if(epoch.current!==token)return;
   onRestored?.();useWorkspaceStore.getState().setWorkspace(result.id,result.name,result.description);navigate("/workspace",{state:{restoredBackup:true}});
  }catch(cause){if(epoch.current===token)setError(cause instanceof Error?cause.message:"Restore failed. Existing work was kept.");}
  finally{if(epoch.current===token)setBusy("");}
 }
 return <section aria-label="Workspace backup and restore" className="border-t border-compass-border pt-6 mt-8 space-y-3 text-sm">
  <h2 className="text-lg font-medium">Backup and restore</h2>
  <p className="text-compass-muted">Backups include saved task history, imported evidence versions and older saved documents. They contain source text and metadata. Keep the file private. Maximum backup size: 250 MB.</p>
  <p className="text-compass-muted">Credentials, source connection paths, native approvals, vector caches and separate output/export files are excluded. A restore creates a new workspace folder; select a backup file, then its parent folder. Reconnect sources before importing again. Unfinished jobs are not replayed.</p>
  <div className="flex flex-wrap gap-3">
   {workspace&&<button disabled={!!busy} className="border border-compass-border rounded px-3 py-2 disabled:opacity-50" onClick={()=>void backup()}>Back up workspace</button>}
   <button disabled={!!busy} className="border border-compass-border rounded px-3 py-2 disabled:opacity-50" onClick={()=>void restore()}>Restore backup into new workspace</button>
  </div>
  {(busy||notice)&&<p role="status">{busy||notice}</p>}{error&&<p role="alert" className="text-red-400">{error}</p>}
 </section>;
}

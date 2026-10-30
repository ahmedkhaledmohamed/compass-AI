import {useState} from "react";
import {FolderOpen,FileText} from "lucide-react";
import {useWorkspaceStore} from "../../stores/workspace";
import type {SourceConfig} from "../../types/engine";

const types=[{id:"docs",name:"Documents"},{id:"interviews",name:"Interviews"},{id:"analytics",name:"Analytics"},{id:"support",name:"Support tickets"},{id:"code",name:"Local code"}];
const style="w-full border border-compass-border bg-compass-card rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-compass-accent disabled:opacity-50";
const defaultKind=(type:string):"directory"|"file"=>["analytics","support"].includes(type)?"file":"directory";

export default function SourceConnector(){
 const workspace=useWorkspaceStore(s=>s.workspacePath),sources=useWorkspaceStore(s=>s.sources),setSources=useWorkspaceStore(s=>s.setSources);
 const [type,setType]=useState("docs"),[name,setName]=useState("");
 const [kind,setKind]=useState<"directory"|"file">("directory");
 const [reconnectKinds,setReconnectKinds]=useState<Record<string,"directory"|"file">>({});
 const [selection,setSelection]=useState<{id:string;label:string}|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState("");
 async function choose(){
  setBusy(true);setError("");
  try{const chosen=await window.compass.workspaces.choose("source",kind);if(chosen){setSelection(chosen);if(!name.trim())setName(chosen.label);}}
  catch(cause){setError(cause instanceof Error?cause.message:"The source picker could not be opened.");}
  finally{setBusy(false);}
 }
 async function connect(source:SourceConfig|undefined,selected:{id:string;label:string}){
  if(!workspace)return;
  const result=await window.compass.engine.call("/connect",{workspace_path:workspace,source_id:source?.id,new_source:!source,source_type:source?.type||type,name:source?.name||name.trim(),path:selected.id}) as {status:string;source_id?:string;accessible?:boolean;message?:string};
  if(result.status!=="ok"||!result.source_id||result.accessible!==true)throw new Error(result.message||"The source could not be connected. Choose a readable local file or folder.");
  const info=await window.compass.engine.call("/workspace/info",{workspace_path:workspace}) as {status:string;sources:SourceConfig[]};
  if(info.status!=="ok")throw new Error("Source connected. Reopen the workspace to reload its source list.");
  setSources(info.sources);
 }
 async function add(event:React.FormEvent){
  event.preventDefault();if(!selection||!name.trim()||busy)return;
  setBusy(true);setError("");
  try{await connect(undefined,selection);setName("");setSelection(null);}
  catch(cause){setError(cause instanceof Error?cause.message:"The source could not be added.");}
  finally{setBusy(false);}
 }
 async function reconnect(source:SourceConfig){
  setBusy(true);setError("");
  try{const selected=await window.compass.workspaces.choose("source",reconnectKinds[source.id||source.name]||source.selection_kind||defaultKind(source.type));if(selected)await connect(source,selected);}
  catch(cause){setError(cause instanceof Error?cause.message:"The source could not be reconnected.");}
  finally{setBusy(false);}
 }
 return <div className="space-y-5">
  {error&&<p role="alert" className="text-red-400 text-sm">{error}</p>}
  {sources.length>0&&<ul className="divide-y divide-compass-border border border-compass-border rounded-lg">
   {sources.map(source=><li key={source.id||source.name} className="p-4 flex items-center gap-3">
    <FileText className="w-4 h-4 shrink-0 text-compass-accent"/>
    <div className="min-w-0 flex-1"><p className="font-medium break-words">{source.name}</p><p className="text-xs text-compass-muted">{types.find(item=>item.id===source.type)?.name||source.type} · {source.approved?"Folder or file approved":"Select its folder or file before importing"}</p></div>
    {["docs","interviews"].includes(source.type)&&<select aria-label={`Reconnect ${source.name} from`} className="bg-compass-card border border-compass-border rounded px-2 py-2 text-sm" disabled={busy} value={reconnectKinds[source.id||source.name]||source.selection_kind||defaultKind(source.type)} onChange={event=>setReconnectKinds(value=>({...value,[source.id||source.name]:event.target.value as "directory"|"file"}))}><option value="directory">Folder</option><option value="file">File</option></select>}
    <button disabled={busy} onClick={()=>reconnect(source)} className="text-sm border border-compass-border rounded px-3 py-2 disabled:opacity-50">Reconnect</button>
   </li>)}
  </ul>}
  <form onSubmit={add} className="border border-compass-border rounded-lg p-4 space-y-4 max-w-2xl">
   <h3 className="font-medium">Add a local source</h3>
   <p className="text-sm text-compass-muted">Give each source its own name. Adding another document folder keeps earlier sources and evidence.</p>
   <div className="grid sm:grid-cols-2 gap-3">
    <label className="text-sm space-y-1 block"><span>Source type</span><select className={style} disabled={busy} value={type} onChange={event=>{setType(event.target.value);setKind(defaultKind(event.target.value));setSelection(null);}}>{types.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label className="text-sm space-y-1 block"><span>Source name</span><input className={style} required maxLength={200} disabled={busy} value={name} onChange={event=>setName(event.target.value)} placeholder="For example, product strategy"/></label>
   </div>
   {["docs","interviews"].includes(type)&&<label className="text-sm space-y-1 block"><span>Import from</span><select className={style} value={kind} disabled={busy} onChange={event=>{setKind(event.target.value as "directory"|"file");setSelection(null);}}><option value="directory">Folder</option><option value="file">File</option></select></label>}
   <button type="button" onClick={choose} disabled={busy} className="flex items-center gap-2 border border-compass-border rounded px-3 py-2 text-sm disabled:opacity-50"><FolderOpen className="w-4 h-4"/>{selection?`Selected: ${selection.label}`:kind==="directory"?"Choose source folder":"Choose source file"}</button>
   <button type="submit" disabled={busy||!selection||!name.trim()} className="bg-compass-accent rounded px-4 py-2 text-sm disabled:opacity-50">{busy?"Connecting…":"Add source"}</button>
  </form>
 </div>;
}

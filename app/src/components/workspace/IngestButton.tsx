import {useEffect,useRef,useState} from "react";
import {useWorkspaceStore} from "../../stores/workspace";
import type {SourceConfig} from "../../types/engine";

type Coverage={entries_scanned:number;files_read:number;bytes_read:number;skipped_files:number;skipped_directories:number;skipped_examples:string[];complete?:boolean;truncated:boolean};
type SourceResult={source_id:string;name:string;state:string;items:number;message?:string;current_file?:string;coverage?:Coverage};
type ImportJob={id:string;state:string;stage:string;message:string;source_ids:string[];current_source:string;cancel_requested?:boolean;draining:boolean;results:SourceResult[]};
const active=(job:ImportJob|null)=>!!job&&(["queued","running"].includes(job.state)||job.draining);
const button="border border-compass-border rounded px-4 py-2 text-sm disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-compass-accent";
const states:Record<string,string>={queued:"Waiting",reading:"Reading",completed:"Imported",failed:"Previous evidence kept",cancelled:"Stopped; previous evidence kept",timed_out:"Timed out; previous evidence kept",interrupted:"Interrupted; previous evidence kept"};

export default function IngestButton(){
 const workspace=useWorkspaceStore(s=>s.workspacePath);
 return workspace?<ImportPanel key={workspace} workspace={workspace}/>:null;
}
function ImportPanel({workspace}:{workspace:string}){
 const sources=useWorkspaceStore(s=>s.sources);
 const [selected,setSelected]=useState<string[]>([]),[job,setJob]=useState<ImportJob|null>(null);
 const [error,setError]=useState(""),[loading,setLoading]=useState(true),[starting,setStarting]=useState(false);
 const version=useRef(0);
 const mounted=useRef(true),inFlight=useRef(false),pending=useRef<{selection:string;id:string}|null>(null);
 const working=active(job);
 function current(){return mounted.current&&useWorkspaceStore.getState().workspacePath===workspace;}
 async function call(endpoint:string,extra:object={}){
  const result=await window.compass.engine.call(`/imports/${endpoint}`,{workspace_path:workspace,...extra}) as {status:string;job:ImportJob|null;detail?:string;message?:string};
  if(result.status!=="ok")throw new Error(result.detail||result.message||"The import could not be updated. Reload its status before retrying.");
  return result.job;
 }
 async function receive(next:ImportJob|null){
  if(!current())return;
  setJob(previous=>next&&previous?.id===next.id&&previous.cancel_requested&&active(next)?{...next,cancel_requested:true}:next);useWorkspaceStore.getState().setIngesting(active(next));
  if(next&&!active(next)){
   const info=await window.compass.engine.call("/workspace/info",{workspace_path:workspace}) as {status:string;evidence_count:number;evidence_summary:Record<string,number>;sources:SourceConfig[]};
   if(current()&&info.status==="ok"){
    useWorkspaceStore.getState().setIngestionResults(next.results.map(item=>({name:item.name,type:"",items:item.items,error:item.state==="failed"?item.message:undefined})),info.evidence_count,info.evidence_summary);
    useWorkspaceStore.getState().setSources(info.sources);
   }
  }
 }
 async function reload(){
  setError("");const token=++version.current;
  try{const next=await call("latest");if(current()&&token===version.current){setSelected(next?.source_ids||[]);await receive(next);}}
  catch(cause){if(current()&&token===version.current)setError(cause instanceof Error?cause.message:"Import status could not be loaded.");}
  finally{if(current()&&token===version.current)setLoading(false);}
 }
 useEffect(()=>{
  mounted.current=true;void reload();
  return()=>{mounted.current=false;version.current++;};
 },[workspace]);
 useEffect(()=>{
  if(!working||!job)return;
  let stopped=false;
  async function poll(){
   if(inFlight.current)return;inFlight.current=true;
   try{const next=await call("status",{job_id:job!.id});if(!stopped)await receive(next);}
   catch(cause){if(!stopped&&current())setError(cause instanceof Error?cause.message:"Could not read import status.");}
   finally{inFlight.current=false;}
  }
  const timer=setInterval(()=>void poll(),500);void poll();
  return()=>{stopped=true;clearInterval(timer);};
 },[job?.id,working]);
 async function start(){
  version.current++;setStarting(true);setError("");useWorkspaceStore.getState().setIngesting(true);
  const selection=JSON.stringify(selected);
  if(!pending.current||pending.current.selection!==selection)pending.current={selection,id:crypto.randomUUID().replace(/-/g,"")};
  try{const next=await call("start",{source_ids:selected,request_id:pending.current.id});pending.current=null;await receive(next);}
  catch(cause){if(current()){setError(cause instanceof Error?cause.message:"Import could not start. Reload status before retrying.");useWorkspaceStore.getState().setIngesting(false);}}
  finally{if(current())setStarting(false);}
 }
 async function cancel(){
  if(!job)return;setError("");
  try{await receive(await call("cancel",{job_id:job.id}));}
  catch(cause){if(current())setError(cause instanceof Error?cause.message:"Cancellation could not be requested. Reload status to reconnect.");}
 }
 return <div className="space-y-4">
  <p className="text-sm text-compass-muted">Select which sources to import. Completed sources are saved separately. Stopping keeps previous evidence for unfinished sources.</p>
  <p className="text-xs text-compass-muted">Supported UTF-8 text, CSV/TSV, JSON and code files. Up to 1,000 files and 20 MB of retained text; 2 MB per file. Binary formats and excluded directories appear in coverage.</p>
  {loading&&<p role="status">Loading import status…</p>}
  {error&&<div role="alert" className="text-red-400 text-sm">{error}<button className={`${button} block mt-2`} onClick={reload}>Reload import status</button></div>}
  <fieldset disabled={loading||starting||working} className="space-y-2"><legend className="font-medium mb-2">Sources to import</legend>
   {sources.length===0&&<p className="text-sm text-compass-muted">Add a source above before importing.</p>}
   {sources.map(source=><label key={source.id||source.name} className="flex gap-3 items-center text-sm"><input type="checkbox" disabled={!source.id||!source.approved} checked={!!source.id&&selected.includes(source.id)} onChange={event=>{pending.current=null;setSelected(value=>event.target.checked?[...value,source.id!]:value.filter(id=>id!==source.id));}}/><span>{source.name}{!source.approved&&<span className="text-compass-muted"> — reconnect before importing</span>}</span></label>)}
  </fieldset>
  <div className="flex gap-3"><button className={`${button} bg-compass-accent`} disabled={loading||starting||working||!selected.length} onClick={start}>{starting?"Starting import…":working?"Import in progress":"Import selected sources"}</button>
   {working&&<button className={button} disabled={!!job?.cancel_requested} onClick={cancel}>{job?.cancel_requested?"Stopping import…":"Stop import"}</button>}
  </div>
  {job&&<section aria-label="Import results" className="border border-compass-border rounded-lg p-4 space-y-4">
   <p role="status" className="font-medium">{working?(job.cancel_requested?"Stopping import…":job.current_source?`Importing ${job.current_source}`:"Preparing import…"):job.state==="completed"?"Import complete":job.state==="partial"?"Import complete with omissions":job.state==="cancelled"?"Import stopped":job.state==="interrupted"?"Import interrupted":job.state==="timed_out"?"Import timed out":"Import failed"}</p>
   {job.message&&<p className="text-sm text-compass-muted">{job.message}</p>}
   {job.results.map(result=><div key={result.source_id} className="border-t border-compass-border pt-3 text-sm space-y-1">
    <p className="font-medium">{result.name}: {states[result.state]||result.state}</p>
    {result.state==="completed"&&<p>{result.items} evidence items saved.</p>}
    {result.message&&<p className="text-red-400">{result.message}</p>}
    {result.coverage&&<><p className="text-compass-muted">{result.coverage.files_read} files read · {result.coverage.bytes_read.toLocaleString()} bytes · {result.coverage.skipped_files} files and {result.coverage.skipped_directories} directories omitted.</p>
     {result.coverage.skipped_examples.length>0&&<details><summary>Omitted file examples</summary><ul className="list-disc pl-5">{result.coverage.skipped_examples.map(file=><li key={file}>{file}</li>)}</ul></details>}
    </>}
   </div>)}
  </section>}
 </div>;
}

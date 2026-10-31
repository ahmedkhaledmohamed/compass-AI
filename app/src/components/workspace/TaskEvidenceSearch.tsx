import {useEffect,useRef,useState} from "react";
import type {RetrievalReceipt,RetrievedPassage} from "../../types/tasks";

export function SavedPassage({workspace,taskId,evidenceId,start,end}:{workspace:string;taskId:string;evidenceId:string;start:number;end:number}){
 const [text,setText]=useState<string|null>(null),[error,setError]=useState("");
 const [loading,setLoading]=useState(false);
 const epoch=useRef(0);
 useEffect(()=>{epoch.current++;setText(null);setError("");setLoading(false);return()=>{epoch.current++;};},[workspace,taskId,evidenceId,start,end]);
 async function open(){
  const token=++epoch.current;setLoading(true);setError("");
  try{const result=await window.compass.engine.call("/tasks/passage",{workspace_path:workspace,task_id:taskId,evidence_id:evidenceId,start,end}) as {status:string;passage:{content:string};message?:string;detail?:string};
   if(result.status!=="ok")throw new Error(result.detail||result.message||"This saved passage could not be opened.");if(epoch.current===token)setText(result.passage.content);
  }catch(cause){if(epoch.current===token)setError(cause instanceof Error?cause.message:"This saved passage could not be opened.");}finally{if(epoch.current===token)setLoading(false);}
 }
 return <div className="mt-2">
  {text===null?<button type="button" disabled={loading} className="text-compass-accent underline text-sm disabled:opacity-50" onClick={open}>{loading?"Opening passage…":"Open saved passage"}</button>:<pre className="whitespace-pre-wrap text-sm font-sans border-l border-compass-border pl-3">{text}</pre>}
  {error&&<p role="alert" className="text-red-400 text-sm">{error}</p>}
 </div>;
}

export default function TaskEvidenceSearch({workspace,taskId}:{workspace:string;taskId:string}){
 const [query,setQuery]=useState(""),[passages,setPassages]=useState<RetrievedPassage[]>([]);
 const [receipt,setReceipt]=useState<RetrievalReceipt|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState("");
 const epoch=useRef(0);
 async function search(value:string|null){
  const token=++epoch.current;setBusy(true);setError("");
  try{
   const result=await window.compass.engine.call("/tasks/retrieve",{workspace_path:workspace,task_id:taskId,query:value}) as {status:string;passages:RetrievedPassage[];receipt:RetrievalReceipt;message?:string;detail?:string};
   if(result.status!=="ok")throw new Error(result.message||result.detail||"Saved passage search failed. Retry without changing the evidence.");
   if(epoch.current===token){setPassages(result.passages);setReceipt(result.receipt);}
  }catch(cause){if(epoch.current===token)setError(cause instanceof Error?cause.message:"Saved passage search failed.");}
  finally{if(epoch.current===token)setBusy(false);}
 }
 useEffect(()=>{void search(null);return()=>{epoch.current++;};},[workspace,taskId]);
 return <section aria-label="Search saved evidence" className="space-y-3">
  <h3 className="font-medium">Search saved evidence</h3>
  <p className="text-sm text-neutral-400">Search stays inside this task’s selected evidence snapshot. Results show passages, not a complete review of every source.</p>
  <form className="flex gap-2" onSubmit={event=>{event.preventDefault();void search(query.trim()||null);}}>
   <label className="flex-1"><span className="sr-only">Search words</span><input maxLength={4000} className="w-full rounded border border-compass-border bg-compass-card px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-compass-accent" value={query} onChange={event=>setQuery(event.target.value)} placeholder="For example, mobile retention"/></label>
   <button className="border border-compass-border rounded px-3 py-2 text-sm disabled:opacity-50" disabled={busy}>Search passages</button>
  </form>
  {busy&&<p role="status" className="text-sm text-neutral-400">Searching saved passages…</p>}
  {error&&<p role="alert" className="text-red-400 text-sm">{error}</p>}
  {!busy&&!error&&receipt&&<p role="status" className="text-sm text-neutral-400">{receipt.status==="empty"?"No saved passages matched these words. Try more specific words or inspect the source previews.":`${passages.length} passages shown from ${receipt.selected_documents} selected documents. ${receipt.status==="complete"?"All saved text fits in this selection.":"Additional saved text was not included."}`}</p>}
  {!busy&&!error&&passages.map(passage=><details key={passage.chunk_id} className="border border-compass-border rounded p-3 text-sm">
   <summary className="cursor-pointer">{passage.source_name||passage.source}: {passage.title} · lines {passage.line_start}–{passage.line_end}</summary>
   <p className="text-xs text-neutral-400 my-2">Saved characters {passage.start}–{passage.end}</p><pre className="whitespace-pre-wrap font-sans">{passage.content}</pre>
  </details>)}
 </section>;
}

/** Export canonical reviewed work through a native save dialog and atomic file write. */
import fs from "fs/promises";
import path from "path";
import {randomUUID} from "crypto";
interface ExportRequest {workspace_path:string;task_id:string;expected_revision:number;kind:"brief"|"handoff";}
interface WorkspaceRequests {request(endpoint:string,body:unknown):Promise<any>;}
export async function saveTaskExport(access:WorkspaceRequests,request:ExportRequest,choose:(name:string)=>Promise<string|null>){
 if(!request||typeof request!=="object"||Object.keys(request).sort().join(",")!=="expected_revision,kind,task_id,workspace_path"||typeof request.workspace_path!=="string"||!/^ws_[a-f0-9]{32}$/.test(request.workspace_path)||typeof request.task_id!=="string"||!/^[a-f0-9]{32}$/.test(request.task_id)||!Number.isSafeInteger(request.expected_revision)||request.expected_revision<1||!["brief","handoff"].includes(request.kind))throw new Error("Choose a saved task revision to export.");
 async function prepare(){
  const result=await access.request("/tasks/export",request);
  if(result.status!=="ok")throw new Error(typeof result.detail==="string"?result.detail:result.message||"This reviewed artifact could not be exported. Reload the task and try again.");
  if(result.filename!==`compass-${request.kind}-${request.task_id}.md`||typeof result.markdown!=="string"||!result.markdown||Buffer.byteLength(result.markdown)>5_000_000)throw new Error("The engine returned an invalid handoff export. Saved work was kept.");
  return result;
 }
 const initial=await prepare();
 const destination=await choose(initial.filename);
 if(!destination)return {status:"cancelled" as const};
 if(!path.isAbsolute(destination)||path.extname(destination).toLowerCase()!==".md")throw new Error("Choose a Markdown (.md) destination for this export.");
 // The dialog may stay open while another actor changes the task or sources.
 const current=await prepare();
 const temporary=path.join(path.dirname(destination),`.compass-export-${randomUUID()}.tmp`);
 let handle:fs.FileHandle|undefined;
 try{
  handle=await fs.open(temporary,"wx",0o600);await handle.writeFile(current.markdown,"utf8");await handle.sync();await handle.close();handle=undefined;
  await fs.rename(temporary,destination);
 }finally{await handle?.close();await fs.rm(temporary,{force:true});}
 return {status:"saved" as const,filename:path.basename(destination)};
}

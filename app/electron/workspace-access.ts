/** Native approvals for workspace writes and explicitly selected local sources. */
import fs from "fs";
import path from "path";
import {randomUUID} from "crypto";

type Identity = {path:string;device:number;inode:number;directory:boolean};
type SourceApproval = Identity & {id?:string;name:string;type:string};
type Workspace = Identity & {id:string;name:string;description:string;created_at:string;last_opened:string;sources:SourceApproval[]};
type Registry = {version:1;active:string|null;workspaces:Workspace[]};
type Engine = (endpoint:string,body?:unknown)=>Promise<any>;
const identifier=()=>randomUUID().replace(/-/g,"");
const supportedSources=new Set(["code","docs","analytics","interviews","support"]);
const safeText=(value:unknown,limit:number)=>{
  if(typeof value!=="string" || value.length>limit || /[\u0000-\u0008]/.test(value))throw new Error("Invalid workspace or source details.");
  return value.trim();
};
function identity(selected:string):Identity {
  let resolved:string,stat:fs.Stats;
  try {resolved=fs.realpathSync(selected);stat=fs.statSync(resolved);}
  catch {throw new Error("The selected file or folder is unavailable. Reconnect its drive or select its original location again.");}
  if(!stat.isDirectory() && !stat.isFile())throw new Error("Choose a regular file or folder.");
  return {path:resolved,device:stat.dev,inode:stat.ino,directory:stat.isDirectory()};
}
function unchanged(record:Identity) {
  const current=identity(record.path);
  if(current.path!==record.path || current.device!==record.device || current.inode!==record.inode || current.directory!==record.directory)throw new Error("The selected folder or file moved or changed identity. Select it again.");
}
function publicWorkspace(record:Workspace) {
  // `path` is a compatibility field for existing views: it is an opaque ID,
  // never a filesystem path. Only the native bridge resolves it.
  return {id:record.id,path:record.id,name:record.name,description:record.description,created_at:record.created_at,last_opened:record.last_opened};
}
function privateStorage(root:string) {
  const dir=path.join(root,".compass");
  if(fs.existsSync(dir) && (!fs.lstatSync(dir).isDirectory() || fs.lstatSync(dir).isSymbolicLink()))throw new Error("Workspace storage cannot be a symbolic link.");
  const entries=["compass.yaml","tasks.sqlite3","tasks.sqlite3-wal","tasks.sqlite3-shm","jobs.lock","knowledge","knowledge/vectors","knowledge/vectors/chroma.sqlite3","knowledge/vectors/chroma.sqlite3-wal","knowledge/vectors/chroma.sqlite3-shm","knowledge/evidence_store.json","knowledge/evidence.sqlite3","knowledge/evidence.sqlite3-wal","knowledge/evidence.sqlite3-shm","documents","output"];
  for(const entry of entries) {
    const target=path.join(dir,entry);
    try {if(fs.lstatSync(target).isSymbolicLink())throw new Error("Workspace storage contains a symbolic link. Restore its original files before opening it.");}
    catch(cause){if((cause as NodeJS.ErrnoException).code!=="ENOENT")throw cause;}
  }
}
export class WorkspaceAccess {
  private selections=new Map<string,{identity:Identity;expires:number;purpose:"workspace"|"source"}>();
  private tail:Promise<unknown>=Promise.resolve();
  constructor(private file:string,private pick:(kind:"directory"|"file")=>Promise<string|null>,private engine:Engine) {}
  private serial<T>(work:()=>Promise<T>):Promise<T> {const next=this.tail.then(work,work);this.tail=next.catch(()=>{});return next;}
  private read():Registry {
    if(!fs.existsSync(this.file))return {version:1,active:null,workspaces:[]};
    try {
      if(fs.lstatSync(this.file).isSymbolicLink() || fs.statSync(this.file).size>1_000_000)throw new Error();
      const data=JSON.parse(fs.readFileSync(this.file,"utf8")) as Registry;
      if(data.version!==1 || !Array.isArray(data.workspaces) || data.workspaces.length>100)throw new Error();
      const ids=new Set<string>();
      for(const record of data.workspaces) {
        if(!/^ws_[a-f0-9]{32}$/.test(record.id) || ids.has(record.id) || !path.isAbsolute(record.path) || !Number.isSafeInteger(record.device) || !Number.isSafeInteger(record.inode) || record.directory!==true || !Array.isArray(record.sources) || record.sources.length>100)throw new Error();
        safeText(record.name,200);safeText(record.description,4000);ids.add(record.id);
        for(const source of record.sources)if(source.id && !/^src_[a-f0-9]{32}$/.test(source.id) || !path.isAbsolute(source.path) || !supportedSources.has(source.type) || typeof source.name!=="string" || !Number.isSafeInteger(source.device) || !Number.isSafeInteger(source.inode))throw new Error();
      }
      if(data.active!==null && !ids.has(data.active))throw new Error();
      return data;
    } catch {throw new Error("The saved workspace registry could not be read. It has been preserved; restore it before continuing.");}
  }
  private write(data:Registry) {
    const encoded=JSON.stringify(data);if(Buffer.byteLength(encoded)>1_000_000)throw new Error("The workspace registry reached its size limit. Remove an unused registration first.");
    fs.mkdirSync(path.dirname(this.file),{recursive:true,mode:0o700});
    const temporary=`${this.file}.${identifier()}.tmp`;
    let fd:number|undefined;
    try {
      fd=fs.openSync(temporary,"wx",0o600);fs.writeFileSync(fd,encoded);fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;fs.renameSync(temporary,this.file);
    } finally {if(fd!==undefined)fs.closeSync(fd);if(fs.existsSync(temporary))fs.unlinkSync(temporary);}
  }
  list(){const data=this.read();return {workspaces:data.workspaces.map(publicWorkspace),activeWorkspaceId:data.active};}
  private record(id:unknown):Workspace {
    if(typeof id!=="string" || !/^ws_[a-f0-9]{32}$/.test(id))throw new Error("Open this workspace through the native folder picker first.");
    const record=this.read().workspaces.find(item=>item.id===id);
    if(!record)throw new Error("This workspace is not registered. Open it through the folder picker.");
    unchanged(record);privateStorage(record.path);
    if(!fs.existsSync(path.join(record.path,".compass","compass.yaml")) || !fs.statSync(path.join(record.path,".compass","compass.yaml")).isFile())throw new Error("Workspace configuration is missing. Reopen its original folder.");
    return record;
  }
  async choose(purpose:"workspace"|"source",kind:"directory"|"file"="directory") {
    if(!["workspace","source"].includes(purpose) || !["directory","file"].includes(kind) || purpose==="workspace" && kind!=="directory")throw new Error("Invalid folder selection.");
    const chosen=await this.pick(kind);if(!chosen)return null;
    const selected=identity(chosen);
    if(selected.directory!==(kind==="directory"))throw new Error("Choose the requested file or folder type.");
    for(const [key,value]of this.selections)if(value.expires<Date.now())this.selections.delete(key);
    if(this.selections.size>=128)throw new Error("Too many pending selections. Finish a selection or restart Compass.");
    const id=`selection_${identifier()}`;this.selections.set(id,{identity:selected,expires:Date.now()+15*60_000,purpose});
    return {id,label:path.basename(selected.path)||selected.path};
  }
  private selected(id:unknown,purpose:"workspace"|"source") {
    if(typeof id!=="string")throw new Error("Choose a folder or file with the native picker.");
    const selected=this.selections.get(id);
    if(!selected || selected.purpose!==purpose || selected.expires<Date.now())throw new Error("This selection expired. Choose it again with the native picker.");
    unchanged(selected.identity);return selected.identity;
  }
  private register(selected:Identity,name:string,description:string) {
    privateStorage(selected.path);
    const data=this.read();let record=data.workspaces.find(item=>item.path===selected.path);
    const now=new Date().toISOString();
    if(record) {
      if(record.device!==selected.device || record.inode!==selected.inode)throw new Error("This workspace changed identity. Remove its old registration before opening it again.");
      record.name=name;record.description=description;record.last_opened=now;
    } else {
      if(data.workspaces.length>=100)throw new Error("Workspace limit reached. Remove an unused registration first.");
      record={...selected,id:`ws_${identifier()}`,name,description,created_at:now,last_opened:now,sources:[]};data.workspaces.push(record);
    }
    data.active=record.id;this.write(data);return publicWorkspace(record);
  }
  create(selection:unknown,nameValue:unknown,descriptionValue:unknown) {
    return this.serial(async()=>{
      this.read();
      const selected=this.selected(selection,"workspace");privateStorage(selected.path);
      const name=safeText(nameValue,200),description=safeText(descriptionValue,4000);if(!name)throw new Error("Enter a product name.");
      const result=await this.engine("/init",{workspace_path:selected.path,name,description});
      if(result.status!=="ok")throw new Error("Workspace could not be created. This folder may already contain a workspace; use Open existing workspace.");
      const registered=this.register(selected,name,description);this.selections.delete(selection as string);return registered;
    });
  }
  open(){return this.serial(async()=>{
    this.read();
    const chosen=await this.pick("directory");if(!chosen)return null;
    const selected=identity(chosen);if(!selected.directory)throw new Error("Choose a workspace folder.");privateStorage(selected.path);
    if(!fs.existsSync(path.join(selected.path,".compass","compass.yaml")))throw new Error("No Compass workspace was found in that folder. Choose its original folder or create a workspace.");
    await this.assertSourceTree(identity(path.join(selected.path,".compass")));
    const result=await this.engine("/workspace/info",{workspace_path:selected.path});
    if(result.status!=="ok")throw new Error("This workspace could not be opened. Its saved files have been kept.");
    unchanged(selected);return this.register(selected,safeText(result.name,200),safeText(result.description||"",4000));
  });}
  activate(id:unknown){return this.serial(async()=>{const record=this.record(id);const data=this.read();data.active=record.id;data.workspaces.find(item=>item.id===id)!.last_opened=new Date().toISOString();this.write(data);return publicWorkspace(record);});}
  forget(id:unknown){return this.serial(async()=>{const readiness=await this.engine("/connections/readiness");if(readiness.status!=="ok" || readiness.busy!==false)throw new Error("Finish or cancel generation before removing a workspace registration.");const data=this.read();if(typeof id!=="string" || !data.workspaces.some(item=>item.id===id))throw new Error("Workspace not found.");data.workspaces=data.workspaces.filter(item=>item.id!==id);if(data.active===id)data.active=null;this.write(data);return this.list();});}
  async request(endpoint:string,body:unknown,invoke:Engine=this.engine) {
    if(endpoint==="/usage")return invoke(endpoint);
    if(!body || typeof body!=="object" || Array.isArray(body))throw new Error("Choose a registered workspace first.");
    const input=body as Record<string,unknown>;
    const record=this.record(input.workspace_path);
    const request={...input,workspace_path:record.path};
    if(endpoint==="/init")throw new Error("Use the native workspace creation flow.");
    if(endpoint==="/connect")return this.serial(async()=>{
      const selected=this.selected(input.path,"source");
      const type=safeText(input.source_type,40),name=safeText(input.name,200);
      if(!supportedSources.has(type) || !name || input.url || input.options || input.auth)throw new Error("Choose a supported local source with the native picker.");
      this.record(record.id);await this.assertSourceTree(selected);
      if(record.sources.length>=100 && !record.sources.some(item=>item.name===name || item.id===input.source_id))throw new Error("Source limit reached. Use a smaller set of sources.");
      const sourceId=input.source_id;
      if(sourceId!==undefined && (typeof sourceId!=="string" || !/^src_[a-f0-9]{32}$/.test(sourceId)))throw new Error("Choose an existing source to reconnect.");
      const result=await invoke(endpoint,{workspace_path:record.path,source_type:type,name,path:selected.path,source_id:sourceId,new_source:input.new_source===true});
      if(result.status!=="ok" || result.accessible!==true)throw new Error(typeof result.detail==="string"&&result.detail.length<500?result.detail:"That source could not be connected. Choose a readable local file or folder.");
      const data=this.read();const current=data.workspaces.find(item=>item.id===record.id)!;
      current.sources=current.sources.filter(item=>result.source_id && item.id?item.id!==result.source_id:item.name!==name);current.sources.push({...selected,id:result.source_id,type,name});this.write(data);this.selections.delete(input.path as string);return result;
    });
    if(endpoint==="/ingest" || endpoint==="/refresh") {
      const info=await this.engine("/workspace/info",{workspace_path:record.path});
      if(info.status!=="ok" || !Array.isArray(info.sources))throw new Error("Source configuration could not be checked.");
      for(const source of info.sources) {
        if(endpoint==="/refresh" && (input.source_id ? source.id!==input.source_id : input.source_name && source.name!==input.source_name))continue;
        const approval=record.sources.find(item=>(item.id?item.id===source.id:item.name===source.name) && item.type===source.type && item.path===source.path);
        if(!approval || source.url || source.auth || Object.keys(source.options||{}).length)throw new Error("Select each source again with the native picker before importing this workspace.");
        unchanged(approval);await this.assertSourceTree(approval);
      }
    }
    const result=await invoke(endpoint,request);
    if(endpoint==="/workspace/info" && Array.isArray(result.sources))return {...result,sources:result.sources.map((source:any)=>{
      const approval=record.sources.find(item=>(item.id?item.id===source.id:item.name===source.name)&&item.type===source.type&&item.path===source.path);
      return {...source,path:null,url:null,options:{},auth:undefined,approved:!!approval&&!source.url&&!source.auth&&!Object.keys(source.options||{}).length,selection_kind:approval?(approval.directory?"directory":"file"):(["analytics","support"].includes(source.type)?"file":"directory")};
    })};
    return result;
  }
  private async assertSourceTree(source:Identity) {
    // Reject symlinks rather than allowing an imported file to escape its approved root.
    const pending=[source.path];let count=0;
    while(pending.length) {
      const target=pending.pop()!;const stat=await fs.promises.lstat(target);
      if(stat.isSymbolicLink())throw new Error("The source contains a symbolic link. Choose a folder with regular local files.");
      if(!stat.isFile() && !stat.isDirectory())throw new Error("The source contains a special file that cannot be imported.");
      if(++count>100_000)throw new Error("This source has too many files. Choose a smaller folder.");
      if(stat.isDirectory()) {
        const directory=await fs.promises.opendir(target);
        for await(const child of directory) {
          if(count+pending.length>=100_000)throw new Error("This source has too many files. Choose a smaller folder.");
          pending.push(path.join(target,child.name));
        }
      }
    }
  }
}

import { create } from "zustand";

export interface WorkspaceEntry {
  id: string;
  name: string;
  description: string;
  path: string;
  created_at: string;
  last_opened: string;
}

interface WorkspaceManagerState {
  workspaces: WorkspaceEntry[];
  activeWorkspaceId: string | null;
  loaded: boolean;
  error:string;

  loadWorkspaces: () => Promise<void>;
  saveWorkspaces: () => Promise<void>;
  addWorkspace: (entry: Omit<WorkspaceEntry, "id" | "created_at" | "last_opened">) => Promise<WorkspaceEntry>;
  removeWorkspace: (id: string) => Promise<void>;
  openWorkspace: (id: string) => Promise<void>;
  pickWorkspace:()=>Promise<WorkspaceEntry|null>;
  getActiveWorkspace: () => WorkspaceEntry | null;
}

// Native registrations are authoritative. Legacy localStorage is preserved,
// but its paths never grant filesystem access.
export const useWorkspaceManager = create<WorkspaceManagerState>((set,get)=>({
  workspaces:[],activeWorkspaceId:null,loaded:false,error:"",
  loadWorkspaces:async()=>{
    try {const data=await window.compass.workspaces.list();set({...data,loaded:true,error:""});}
    catch {set({loaded:true,error:"Saved workspace registrations could not be read. Restore the registry or reopen Compass; your files have been kept."});}
  },
  saveWorkspaces:async()=>{await get().loadWorkspaces();},
  addWorkspace:async entry=>{
    await get().loadWorkspaces();
    if(get().error)throw new Error(get().error);
    const registered=get().workspaces.find(item=>item.id===entry.path);
    if(!registered)throw new Error("Create or open this workspace through the native folder picker.");
    return registered;
  },
  removeWorkspace:async id=>{
    const result=await window.compass.workspaces.forget(id);set({...result,loaded:true});
  },
  openWorkspace:async id=>{
    try {await window.compass.workspaces.activate(id);await get().loadWorkspaces();}
    catch(cause){set({error:cause instanceof Error?cause.message:"This workspace could not be opened."});throw cause;}
  },
  pickWorkspace:async()=>{
    const selected=await window.compass.workspaces.open();
    if(selected)await get().loadWorkspaces();
    return selected;
  },
  getActiveWorkspace:()=>get().workspaces.find(item=>item.id===get().activeWorkspaceId)||null,
}));

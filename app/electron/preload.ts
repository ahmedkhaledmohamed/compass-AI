import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("compass", {
  workspaces: {
    list:()=>ipcRenderer.invoke("workspaces-list"),
    choose:(purpose:string,kind:string)=>ipcRenderer.invoke("workspaces-choose",purpose,kind),
    create:(selection:string,name:string,description:string)=>ipcRenderer.invoke("workspaces-create",selection,name,description),
    open:()=>ipcRenderer.invoke("workspaces-open"),
    activate:(id:string)=>ipcRenderer.invoke("workspaces-activate",id),
    forget:(id:string)=>ipcRenderer.invoke("workspaces-forget",id),
  },
  ai: {
    status: () => ipcRenderer.invoke("ai-status"),
    list: () => ipcRenderer.invoke("ai-list"),
    signIn: (provider: string, id?: string) => ipcRenderer.invoke("ai-sign-in",provider,id),
    cancel: () => ipcRenderer.invoke("ai-cancel"),
    importSavedClaude: () => ipcRenderer.invoke("ai-import-saved-claude"),
    saveKey: (provider: string, key: string) => ipcRenderer.invoke("ai-save-key",provider,key),
    models: (id: string) => ipcRenderer.invoke("ai-models",id),
    routes: (id: string, model: string) => ipcRenderer.invoke("ai-routes",id,model),
    activate: (id: string, model: string, serving: string) => ipcRenderer.invoke("ai-activate",id,model,serving),
    disconnect: (id: string) => ipcRenderer.invoke("ai-disconnect",id),
    forget: (id: string) => ipcRenderer.invoke("ai-forget",id),
  },
  engine: {
    call: (endpoint: string, body?: unknown) =>
      ipcRenderer.invoke("engine-call", endpoint, body),
    health: () => ipcRenderer.invoke("engine-health"),
    restart: () => ipcRenderer.invoke("engine-restart"),
    onStatus: (callback: (data: { state: string; message: string }) => void) => {
      const handler = (_event: unknown, data: { state: string; message: string }) => callback(data);
      ipcRenderer.on("engine-status", handler);
      return () => ipcRenderer.removeListener("engine-status", handler);
    },
  },
  app: {
    exportTask: (request: {workspace_path:string;task_id:string;expected_revision:number;kind:"brief"|"handoff"}) => ipcRenderer.invoke("task-export",request),
    saveFile: (defaultName: string, content: string) =>
      ipcRenderer.invoke("save-file", defaultName, content),

  },
});

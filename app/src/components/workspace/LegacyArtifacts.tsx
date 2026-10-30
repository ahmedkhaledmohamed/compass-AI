import { useEffect, useState } from "react";

type SavedDocument = {id:string;title:string;doc_type:string;content_markdown:string;content_json:unknown};

export default function LegacyArtifacts({workspacePath}:{workspacePath:string}) {
  const [documents,setDocuments]=useState<SavedDocument[]>([]);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [loading,setLoading]=useState(true);
  useEffect(()=>{
    let alive=true;
    setDocuments([]);setError("");setNotice("");setLoading(true);
    window.compass.engine.call("/documents/list",{workspace_path:workspacePath}).then(value=>{
      const result=value as {status:string;documents:SavedDocument[]};
      if(result.status!=="ok" || !Array.isArray(result.documents))throw new Error("Saved artifacts could not be loaded.");
      if(alive)setDocuments(result.documents);
    }).catch(()=>{if(alive)setError("Saved artifacts could not be loaded. Reopen the workspace to try again; saved files have been kept.");}).finally(()=>{if(alive)setLoading(false);});
    return()=>{alive=false;};
  },[workspacePath]);
  async function exportDocument(document:SavedDocument) {
    setError("");setNotice("");
    try {
      const stem=document.title.replace(/[^a-zA-Z0-9 _-]/g,"").trim().slice(0,80)||"saved-artifact";
      const content=document.content_markdown || `# ${document.title}\n\nSaved ${document.doc_type} artifact. Original structured content:\n\n\`\`\`json\n${JSON.stringify(document.content_json,null,2)}\n\`\`\`\n`;
      const saved=await window.compass.app.saveFile(`${stem}.md`,content);
      if(saved)setNotice("Markdown copy saved. The original artifact is unchanged.");
    } catch {setError("The artifact could not be exported. Try another destination.");}
  }
  return <section aria-label="Previously saved artifacts" className="mt-8 border-t border-compass-border pt-6">
    <h2 className="text-lg font-medium mb-2">Previously saved artifacts</h2>
    <p className="text-sm text-compass-muted mb-4">Review or export earlier documents here. Your original files stay in this workspace.</p>
    {loading && <p role="status">Loading saved artifacts…</p>}
    {error && <p role="alert" className="text-red-400">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!loading && !error && documents.length===0 && <p className="text-sm text-compass-muted">No earlier documents saved.</p>}
    {documents.map(document=><details key={document.id} className="border border-compass-border rounded-lg p-3 my-2">
      <summary>{document.title} · {document.doc_type}</summary>
      <pre className="whitespace-pre-wrap break-words text-sm my-4">{document.content_markdown || JSON.stringify(document.content_json,null,2)}</pre>
      <button onClick={()=>exportDocument(document)} className="border border-compass-border rounded px-3 py-2 text-sm">Export Markdown copy</button>
    </details>)}
  </section>;
}

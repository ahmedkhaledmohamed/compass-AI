import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useWorkspaceStore } from "../stores/workspace";
import { useWorkspaceManager } from "../stores/workspaceManager";
import type { Evidence } from "../types/engine";
import type { Artifact, ArtifactKind, DecisionTask, DraftBody, GenerationJob, TaskSummary } from "../types/tasks";

const input = "w-full rounded-md border border-compass-border bg-compass-card p-3 text-compass-text focus:outline-none focus:ring-2 focus:ring-compass-accent disabled:opacity-60";
const button = "rounded-md border border-compass-border px-3 py-2 text-sm hover:bg-compass-card focus-visible:outline focus-visible:outline-2 focus-visible:outline-compass-accent disabled:opacity-40 disabled:cursor-not-allowed";
const stages: Record<string, string> = { queued: "Waiting to generate", validating: "Checking task and sources", generating: "Waiting for AI response", validating_output: "Checking citations and draft", committing: "Saving draft" };
const primary = `${button} bg-compass-accent text-white hover:bg-compass-accent-hover`;

async function call<T>(workspace: string, endpoint: string, body: object = {}): Promise<T> {
  const result = await window.compass.engine.call(`/tasks/${endpoint}`, { workspace_path: workspace, ...body }) as T & { status?: string; detail?: string; message?: string };
  if (result.status !== "ok") throw new Error(result.detail || result.message || "The task could not be updated. Try reloading it.");
  return result;
}

export default function WorkPage() {
  const workspace = useWorkspaceStore((state) => state.workspacePath);
  const setWorkspace = useWorkspaceStore((state) => state.setWorkspace);
  const workspaces = useWorkspaceManager((state) => state.workspaces);
  const activeId = useWorkspaceManager((state) => state.activeWorkspaceId);
  useEffect(() => {
    const active = workspaces.find((item) => item.id === activeId);
    if (!workspace && active) setWorkspace(active.path, active.name, active.description);
  }, [workspace, workspaces, activeId, setWorkspace]);
  if (!workspace) return <div className="p-10"><h1 className="text-2xl font-semibold mb-3">Work</h1><p>Open a workspace to start a decision task.</p><Link className="text-compass-accent underline" to="/workspace">Open workspace</Link></div>;
  return <TaskWorkspace key={workspace} workspace={workspace} />;
}

function TaskWorkspace({ workspace }: { workspace: string }) {
  const { taskId } = useParams();
  const navigate = useNavigate();
  const epoch = useRef(0);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [sources, setSources] = useState<Array<{ name: string; items: number }>>([]);
  const [task, setTask] = useState<DecisionTask | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const working = !!job && (["queued", "running"].includes(job.state) || job.draining);
  const [stale, setStale] = useState(false);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [question, setQuestion] = useState("");
  const [reader, setReader] = useState("");
  const [completion, setCompletion] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const token = ++epoch.current;
    setTask(null); setJob(null); setEvidence([]); setError(""); setBusy("Loading work…");
    async function load() {
      try {
        const [list, available] = await Promise.all([
          call<{ tasks: TaskSummary[] }>(workspace, "list"),
          call<{ sources: Array<{ name: string; items: number }> }>(workspace, "sources"),
        ]);
        if (epoch.current !== token) return;
        setTasks(list.tasks); setSources(available.sources);
        if (taskId) {
          let [result, saved, pending] = await Promise.all([
            call<{ task: DecisionTask; stale: boolean }>(workspace, "get", { task_id: taskId }),
            call<{ items: Evidence[] }>(workspace, "evidence", { task_id: taskId }),
            call<{ job: GenerationJob | null }>(workspace, "jobs/latest", { task_id: taskId }),
          ]);
          if (epoch.current !== token) return;
          if (pending.job?.result_revision && pending.job.result_revision > result.task.revision) {
            result = await call<{ task: DecisionTask; stale: boolean }>(workspace, "get", { task_id: taskId });
            if (epoch.current !== token) return;
          }
          setTask(result.task); setStale(result.stale); setEvidence(saved.items); setJob(pending.job);
        }
      } catch (cause) {
        if (epoch.current === token) setError(cause instanceof Error ? cause.message : "Could not load work.");
      } finally { if (epoch.current === token) setBusy(""); }
    }
    void load();
    return () => { epoch.current++; };
  }, [workspace, taskId, reload]);

  async function receiveJob(next: GenerationJob, token: number) {
    if (!["queued", "running"].includes(next.state) && !next.draining) {
      const [saved, list] = await Promise.all([
        call<{ task: DecisionTask; stale: boolean }>(workspace, "get", { task_id: next.task_id }),
        call<{ tasks: TaskSummary[] }>(workspace, "list"),
      ]);
      if (epoch.current !== token) return;
      setTask(saved.task); setStale(saved.stale); setTasks(list.tasks);
    }
    if (epoch.current === token) setJob(next);
  }

  useEffect(() => {
    if (!job || !working || !taskId) return;
    const token = epoch.current;
    const jobId = job.id;
    let stopped = false;
    let inFlight = false;
    async function poll() {
      if (inFlight) return;
      inFlight = true;
      try {
        const result = await call<{ job: GenerationJob }>(workspace, "jobs/status", { task_id: taskId, job_id: jobId });
        if (stopped || epoch.current !== token) return;
        await receiveJob(result.job, token);
      } catch (cause) {
        if (!stopped && epoch.current === token) setError(cause instanceof Error ? cause.message : "Could not read generation status. Reload to reconnect.");
      } finally { inFlight = false; }
    }
    void poll();
    const interval = setInterval(() => void poll(), 500);
    return () => { stopped = true; clearInterval(interval); };
  }, [job?.id, working, workspace, taskId]);

  async function cancel() {
    if (!task || !job) return;
    const token = epoch.current;
    setBusy("Cancelling generation…");
    try {
      const result = await call<{ job: GenerationJob }>(workspace, "jobs/cancel", { task_id: task.id, job_id: job.id });
      if (epoch.current === token) await receiveJob(result.job, token);
    } catch (cause) { if (epoch.current === token) setError(cause instanceof Error ? cause.message : "Could not cancel generation."); }
    finally { if (epoch.current === token) setBusy(""); }
  }

  async function create(event: React.FormEvent) {
    event.preventDefault();
    const token = epoch.current;
    setBusy("Saving task…"); setError("");
    try {
      const result = await call<{ task: DecisionTask }>(workspace, "create", {
        question, reader, completion_condition: completion, source_names: selected,
      });
      if (epoch.current === token) navigate(`/work/${result.task.id}`);
    } catch (cause) { if (epoch.current === token) setError(cause instanceof Error ? cause.message : "Could not save task."); }
    finally { if (epoch.current === token) setBusy(""); }
  }

  async function operate(endpoint: string, kind: ArtifactKind, extra: object = {}) {
    if (!task) return;
    const token = epoch.current;
    setBusy(endpoint === "generate" ? "Generating draft…" : "Saving review…"); setError("");
    try {
      if (endpoint === "generate") {
        const result = await call<{ job: GenerationJob }>(workspace, "jobs/start", {
          task_id: task.id, expected_revision: task.revision, kind, request_id: crypto.randomUUID(),
        });
        if (epoch.current === token) await receiveJob(result.job, token);
        return;
      }
      const result = await call<{ task: DecisionTask; stale: boolean }>(workspace, endpoint, {
        task_id: task.id, expected_revision: task.revision, kind, ...extra,
      });
      if (epoch.current !== token) return;
      setTask(result.task); setStale(result.stale);
      const list = await call<{ tasks: TaskSummary[] }>(workspace, "list");
      if (epoch.current === token) setTasks(list.tasks);
    } catch (cause) { if (epoch.current === token) setError(cause instanceof Error ? cause.message : "Could not update task. Reload before trying again."); }
    finally { if (epoch.current === token) setBusy(""); }
  }

  return <div className="min-h-full text-compass-text">
    <header className="flex justify-between items-center px-8 py-6 border-b border-compass-border">
      <div><h1 className="text-2xl font-semibold tracking-tight">Work</h1><p className="mt-1 text-sm text-neutral-400">Evidence, a reviewed decision, then a handoff.</p></div>
      <button className={button} disabled={!!busy || working} onClick={() => navigate("/work")}>New task</button>
    </header>
    <div className="flex min-h-[calc(100vh-100px)]">
      <nav aria-label="Saved tasks" className="w-60 shrink-0 border-r border-compass-border p-3 space-y-1">
        <h2 className="px-3 py-2 text-sm font-medium text-neutral-400">Saved tasks</h2>
        {tasks.length === 0 && <p className="px-3 text-sm text-neutral-400">Your saved decisions will appear here.</p>}
        {tasks.map((item) => <button key={item.id} disabled={!!busy || working} onClick={() => navigate(`/work/${item.id}`)} aria-current={item.id === taskId ? "page" : undefined} className={`block w-full rounded-md p-3 text-left text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-compass-accent ${item.id === taskId ? "bg-compass-card" : "hover:bg-compass-card/60"}`}>
          <span className="block font-medium line-clamp-3">{item.question}</span><span className="block mt-2 text-xs text-neutral-400">{item.handoff_state ? `Handoff ${item.handoff_state}` : item.brief_state ? `Brief ${item.brief_state}` : "Evidence selected"}</span>
        </button>)}
      </nav>
      <div className="flex-1 min-w-0 px-8 py-7 max-w-4xl">
        <p role="status" aria-live="polite" className="text-sm text-neutral-400 mb-3">{busy}</p>
        {job && <div role="status" aria-live="polite" className="mb-5 border-l-2 border-compass-accent pl-4 space-y-2 text-sm">
          <p>{job.state === "running" || job.state === "queued" ? (stages[job.stage] || "Generating draft") : job.state === "succeeded" ? "Draft saved for your review." : job.message}</p>
          {job.draining && <p className="text-neutral-400">The provider request is still stopping. Its result will not be saved; wait before starting another generation.</p>}
          {["queued", "running"].includes(job.state) && <><p className="text-neutral-400">Cancelling prevents this draft from being saved. An already-sent request may still incur provider usage.</p><button className={button} disabled={!!busy} onClick={() => void cancel()}>Cancel generation</button></>}
        </div>}
        {error && <div role="alert" className="mb-6 rounded-md border border-red-400/50 p-4 text-red-200">{error}<button className={`${button} block mt-3`} onClick={() => setReload((value) => value + 1)}>Reload task</button></div>}
        {!taskId && <form onSubmit={create} className="space-y-5 max-w-2xl">
          <h2 className="text-xl font-medium">Start with a decision</h2>
          <label className="block space-y-2"><span>What do you need to decide?</span><textarea className={input} required maxLength={4000} rows={3} value={question} onChange={(event) => setQuestion(event.target.value)} disabled={!!busy || working} /></label>
          <label className="block space-y-2"><span>Who will use this brief?</span><input className={input} required maxLength={1000} value={reader} onChange={(event) => setReader(event.target.value)} disabled={!!busy || working} /></label>
          <label className="block space-y-2"><span>What would a useful result let you do?</span><input className={input} required maxLength={2000} value={completion} onChange={(event) => setCompletion(event.target.value)} disabled={!!busy || working} /></label>
          <fieldset disabled={!!busy || working} className="space-y-3"><legend className="mb-2 font-medium">Select evidence sources</legend>
            {sources.length === 0 && <p className="text-neutral-400">Import evidence in <Link className="underline text-compass-accent" to="/workspace">Workspace</Link> before creating a task.</p>}
            {sources.map((source) => <label key={source.name} className="flex items-center gap-3"><input type="checkbox" className="accent-indigo-500 focus-visible:outline focus-visible:outline-2" checked={selected.includes(source.name)} onChange={(event) => setSelected(event.target.checked ? [...selected, source.name] : selected.filter((name) => name !== source.name))} /><span>{source.name} <span className="text-neutral-400 text-sm">({source.items} items)</span></span></label>)}
          </fieldset>
          <p className="text-sm text-neutral-400">Creating a task saves its evidence selection locally. Generating a draft sends the selected evidence to your configured AI provider.</p>
          <button type="submit" className={primary} disabled={!!busy || working || selected.length === 0}>Save task and inspect evidence</button>
        </form>}
        {task && <div className="space-y-7">
          <div><h2 className="text-2xl leading-snug font-medium max-w-[36ch]">{task.question}</h2><p className="mt-3 text-neutral-400 text-sm">For {task.reader}</p><p className="mt-1 text-sm">Useful result: {task.completion_condition}</p></div>
          {stale && <div role="status" className="border-l-4 border-amber-400 pl-4 text-amber-200">Selected sources have changed. This task keeps its original evidence and edits. Start a new task to investigate the updated sources.</div>}
          <details className="border-y border-compass-border py-4"><summary className="cursor-pointer font-medium">Selected evidence ({evidence.length} {evidence.length === 1 ? "item" : "items"})</summary><div className="mt-4 space-y-3">{evidence.map((item) => <details key={item.id}><summary className="cursor-pointer text-sm">{item.title} <span className="text-neutral-400">({item.source_type})</span></summary><pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap font-sans text-sm text-neutral-300">{item.content}</pre></details>)}</div></details>
          {!task.brief && <button className={primary} disabled={!!busy || working || stale} onClick={() => void operate("generate", "brief")}>Generate decision brief</button>}
          {(["brief", "handoff"] as const).map((kind) => task[kind] && <ArtifactReview key={`${task.id}-${task.revision}-${kind}`} kind={kind} artifact={task[kind]!} evidence={evidence} disabled={!!busy || working} stale={stale} onAction={(endpoint, extra) => void operate(endpoint, kind, extra)} />)}
          {task.brief?.state === "accepted" && !task.handoff && <button className={primary} disabled={!!busy || working || stale} onClick={() => void operate("generate", "handoff")}>Draft specification handoff</button>}
        </div>}
      </div>
    </div>
  </div>;
}

function ArtifactReview({ kind, artifact, evidence, disabled, stale, onAction }: {
  kind: ArtifactKind; artifact: Artifact; evidence: Evidence[]; disabled: boolean; stale: boolean;
  onAction: (endpoint: string, extra?: object) => void;
}) {
  const [body, setBody] = useState<DraftBody>(() => structuredClone(artifact.body));
  const [decision, setDecision] = useState(artifact.decision);
  const dirty = JSON.stringify(body) !== JSON.stringify(artifact.body);
  const reviewed = artifact.reviews.every((review) => review !== "unreviewed") && artifact.reviews.includes("accepted");
  return <section aria-label={kind === "brief" ? "Decision brief" : "Specification handoff"} className="space-y-5">
    <div className="flex justify-between items-start gap-4"><div><h3 className="text-lg font-semibold">{kind === "brief" ? "Decision brief" : "Specification handoff"}</h3><p className="text-sm text-neutral-400 mt-1">{artifact.state === "accepted" ? "Accepted by you" : "Draft for your review"}. Model: {artifact.model}</p></div><button className={button} disabled={disabled || stale || dirty} onClick={() => onAction("generate")}>Generate a new draft</button></div>
    <label className="block space-y-2"><span className="text-sm">Title</span><input className={input} value={body.title} disabled={disabled} onChange={(event) => setBody({ ...body, title: event.target.value })} /></label>
    <label className="block space-y-2"><span className="text-sm">Summary to review</span><textarea className={input} rows={3} disabled={disabled} value={body.summary} onChange={(event) => setBody({ ...body, summary: event.target.value })} /></label>
    <div><h4 className="font-medium mb-3">Review each claim</h4><p className="text-sm text-neutral-400 mb-4">Check that each cited passage supports the claim. Exact quotes have been checked; their meaning still needs your review.</p>
      {body.claims.map((claim, index) => <div key={index} className="border-t border-compass-border py-5 space-y-3">
        <div className="flex justify-between text-sm"><span>{claim.kind === "supported" ? "Evidence-based claim" : claim.kind === "inference" ? "Inference" : claim.kind === "proposal" ? "Proposal" : "Unknown"}</span><span>{artifact.reviews[index]}</span></div>
        <label className="block"><span className="sr-only">Claim {index + 1}</span><textarea className={input} rows={2} disabled={disabled} value={claim.text} onChange={(event) => setBody({ ...body, claims: body.claims.map((item, i) => i === index ? { ...item, text: event.target.value } : item) })} /></label>
        {claim.citations.map((citation, number) => {
          const source = evidence.find((item) => item.id === citation.evidence_id);
          return <details key={number} className="text-sm border-l-2 border-compass-accent pl-3"><summary className="cursor-pointer">Supporting passage: {source?.title || citation.evidence_id}</summary><blockquote className="mt-2 whitespace-pre-wrap text-neutral-300">{citation.quote}</blockquote><p className="mt-2 text-xs text-neutral-400">{source?.source_name || source?.connector} ({source?.source_type}). Saved passage {citation.start}–{citation.end}. {source?.ingested_at && !source.metadata.legacy_ingested_at_unknown ? `Imported ${new Date(source.ingested_at).toLocaleDateString()}.` : "Import date unknown."}</p></details>;
        })}
        <div className="flex gap-2"><button className={button} disabled={disabled || dirty} onClick={() => onAction("review", { claim_index: index, decision: "accepted" })}>Keep claim</button><button className={button} disabled={disabled || dirty} onClick={() => onAction("review", { claim_index: index, decision: "rejected" })}>Reject claim</button></div>
      </div>)}
    </div>
    <label className="block space-y-2"><span>Proposed action</span><textarea className={input} rows={3} disabled={disabled} value={body.proposed_action} onChange={(event) => setBody({ ...body, proposed_action: event.target.value })} /></label>
    <label className="block space-y-2"><span>Alternatives (one per line)</span><textarea className={input} rows={2} disabled={disabled} value={body.alternatives.join("\n")} onChange={(event) => setBody({ ...body, alternatives: event.target.value.split("\n") })} /></label>
    <label className="block space-y-2"><span>Unknowns (one per line)</span><textarea className={input} rows={2} disabled={disabled} value={body.unknowns.join("\n")} onChange={(event) => setBody({ ...body, unknowns: event.target.value.split("\n") })} /></label>
    {dirty && <div className="flex items-center gap-3"><button className={primary} disabled={disabled} onClick={() => onAction("revise", { body })}>Save corrections</button><p className="text-sm text-neutral-400">Saving resets claim review and any later handoff.</p></div>}
    <div className="border-t border-compass-border pt-5 space-y-3">
      <label className="block space-y-2"><span>{kind === "brief" ? "Your chosen direction" : "Your handoff decision"}</span><textarea className={input} rows={2} disabled={disabled} value={decision} onChange={(event) => setDecision(event.target.value)} /></label>
      <button className={primary} disabled={disabled || dirty || stale || !reviewed || !decision.trim()} onClick={() => onAction("accept", { decision })}>{kind === "brief" ? "Accept reviewed brief" : "Accept reviewed handoff"}</button>
    </div>
  </section>;
}

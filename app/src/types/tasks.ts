export type Review = "unreviewed" | "accepted" | "rejected";
export type ArtifactKind = "brief" | "handoff";
export interface Citation { evidence_id: string; start: number; end: number; quote: string }
export interface Claim {
  text: string;
  kind: "supported" | "inference" | "proposal" | "unknown";
  citations: Citation[];
}
export interface DraftBody {
  title: string;
  summary: string;
  claims: Claim[];
  proposed_action: string;
  alternatives: string[];
  unknowns: string[];
}
export interface Artifact {
  body: DraftBody;
  reviews: Review[];
  state: "draft" | "accepted";
  decision: string;
  model: string;
  provider: string;
  edited_by_user: boolean;
}
export interface DecisionTask {
  id: string;
  revision: number;
  question: string;
  reader: string;
  completion_condition: string;
  source_names: string[];
  evidence_snapshot: string;
  brief: Artifact | null;
  handoff: Artifact | null;
}
export interface TaskSummary {
  id: string;
  question: string;
  updated_at: string;
  brief_state: string | null;
  handoff_state: string | null;
}
export interface GenerationJob {
  id: string;
  task_id: string;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "timed_out" | "interrupted";
  stage: string;
  model: string;
  provider: string;
  result_revision: number | null;
  error_code: string | null;
  message: string | null;
  draining: boolean;
}

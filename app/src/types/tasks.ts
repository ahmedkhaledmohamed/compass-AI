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
export interface PassageLocator {
  evidence_id:string;document_id:string;version:string;chunk_id:string;start:number;end:number;line_start:number;line_end:number;rank:number;score?:number|null;reason:"all_saved_text"|"keyword_ranked"|"accepted_brief";
}
export interface RetrievalReceipt {
  version:string;snapshot_id:string;status:"complete"|"partial"|"empty";query_terms:string[];query_truncated:boolean;
  selected_documents:number;total_passages:number;selection_mode:"all_saved_text"|"keyword_ranked";matched_passages:number|null;total_characters:number;provided_characters:number;
  import_omission_documents:number;unknown_import_documents:number;passages:PassageLocator[];
}
export interface RetrievedPassage {
  id:string;document_id:string;version:string;chunk_id:string;source:string;source_name:string;source_type:string;title:string;
  start:number;end:number;line_start:number;line_end:number;content:string;
}
export interface Artifact {
  body: DraftBody;
  reviews: Review[];
  state: "draft" | "accepted";
  decision: string;
  model: string;
  provider: string;
  input_tokens?: number | null;
  output_tokens?: number | null;
  edited_by_user: boolean;
  retrieval?:RetrievalReceipt|null;
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

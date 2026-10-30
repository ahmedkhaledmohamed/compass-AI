/** TypeScript mirrors of Python Pydantic models in engine/compass/models/ */

export type SourceType = "code" | "docs" | "data" | "judgment";

export const SOURCE_TYPE_META: Record<
  SourceType,
  { label: string; color: string; question: string }
> = {
  code: { label: "Code", color: "text-compass-code", question: "What CAN happen?" },
  docs: { label: "Docs", color: "text-compass-docs", question: "What's EXPECTED?" },
  data: { label: "Data", color: "text-compass-data", question: "What IS happening?" },
  judgment: { label: "Judgment", color: "text-compass-judgment", question: "What SHOULD happen?" },
};

export interface Evidence {
  id: string;
  source_type: SourceType;
  connector: string;
  title: string;
  content: string;
  metadata: Record<string, unknown>;
  timestamp: string;
  source_name?: string;
  ingested_at?: string;
}

export type ConflictSeverity = "high" | "medium" | "low";

export type ConflictType =
  | "code_vs_docs"
  | "code_vs_data"
  | "code_vs_judgment"
  | "docs_vs_data"
  | "docs_vs_judgment"
  | "data_vs_judgment";

export interface Conflict {
  conflict_type: ConflictType;
  severity: ConflictSeverity;
  title: string;
  description: string;
  source_a_evidence: string[];
  source_b_evidence: string[];
  recommendation: string;
}

export interface ConflictReport {
  conflicts: Conflict[];
}

export type Confidence = "high" | "medium" | "low";

export interface Opportunity {
  rank: number;
  title: string;
  description: string;
  confidence: Confidence;
  evidence_summary: string;
  evidence_ids: string[];
  conflict_ids: string[];
  estimated_impact: string;
}

export interface AgentTask {
  number: number;
  title: string;
  context: string;
  acceptance_criteria: string[];
  files_to_modify: string[];
  tests: string;
}

export interface FeatureSpec {
  title: string;
  opportunity: Opportunity;
  problem_statement: string;
  proposed_solution: string;
  ui_changes: string;
  data_model_changes: string;
  tasks: AgentTask[];
  success_metrics: string[];
  evidence_citations: string[];
  markdown?: string;
}

export interface AuthConfig {
  method: "oauth" | "api_key" | "pat";
  credential_ref: string;
  scopes: string[];
}

export interface SourceConfig {
  type: string;
  name: string;
  path: string | null;
  url: string | null;
  options: Record<string, unknown>;
  auth?: AuthConfig | null;
}

export interface ProductConfig {
  name: string;
  description: string;
  sources: SourceConfig[];
  model: string;
}

export interface HealthResponse {
  status: string;
  version: string;
}

export interface UsageResponse {
  session_tokens: { input: number; output: number };
  total_cost_estimate: string;
}

export interface EngineError {
  status: "error";
  message: string;
}

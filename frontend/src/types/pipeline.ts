/** 实际管线HTTP数据合同；演示数据不混入此类型。 */
export type ActualMetrics = { n: number; mae: number; rmse: number; r2?: number | null };
export type ResearchEvent = {
  id: number; kind: "phase" | "crawl"; created_at: string; message: string;
  index?: number; total?: number; url?: string; status?: "fetching" | "ok" | "skipped";
  reason?: string;
};
export type PipelineProgress = {
  stage: string; message: string; completed: number; total: number;
  succeeded: number; failed: number; events: ResearchEvent[];
};
export type SeriesReport = {
  scope: string; status: string; requirements: string[]; directory: string;
  split?: { train: number; validation: number; test: number };
  gan: { status: string; synthetic_rows?: number; requested_synthetic_rows?: number; reason?: string; user_accepted?: boolean; used_for_final_model?: boolean; validation_improved?: boolean };
  selected_model?: string; test?: { selected: ActualMetrics; real_only: ActualMetrics; naive: ActualMetrics };
  forecast?: { target_period: number; prediction: number; status: string };
};
export type CandidateProfile = {
  rows: number; exact_duplicate_candidates: number; source_records: number;
  fields: Record<string, { label: string; missing: number; invalid: number; valid: number; missing_fraction: number | null }>;
};
export type DataQuality = {
  counts: Record<string, number>; before: CandidateProfile; after: CandidateProfile; usable: CandidateProfile;
  interpretation: string; llm_status: string;
};
export type PipelineResult = {
  stage: "branch_choice" | "materials_ready" | "choice" | "gan_review" | "completed"; run_id: string;
  extraction?: "collection" | "textbook" | "llm" | "deterministic";
  collection?: { note: string; documents: { record_id: string; url: string; title?: string | null; raw_available: boolean; files: { path: string; sha256: string; bytes: number }[] }[] };
  extraction_detail?: { mode: string; method?: string; rule_count: number; issues: { code: string; message: string }[] } | null;
  context: { question: string };
  counts: Record<string, number>; can_model: boolean; gan_available: boolean; gan_usable: boolean;
  access: { url: string; status: string; reason: string }[]; warning?: string;
  llm_cleaning?: { status: string; model: string; reason?: string; source_validated_rows?: number; invalid_rows?: number } | null;
  data_quality?: DataQuality | null;
  diagnostics?: { code: string; message: string; rejection_reasons: { code: string; message: string; count: number }[] } | null;
  modeling: { status: string; series_reports: SeriesReport[]; requirements: string[]; gan_gate?: { status: string; reason: string; min_real_train_samples: number; sample_definition: string } };
  files: Record<string, string>; report: string | null;
  llm: { status: string; reason?: string; reply?: { content: string }; memory_enabled?: boolean; analysis_scope?: string } | null;
};
export type PipelineOperation = { operation_id: string; state: "queued" | "running" | "done" | "failed"; result: PipelineResult | null; error: { message: string } | null; progress?: PipelineProgress | null };

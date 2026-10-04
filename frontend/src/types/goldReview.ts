export type EvidenceStatus = "resolved" | "multi_chunk" | "parser_issue" | "manual_review" | "unresolved";

export interface Candidate {
  chunk_ids: string[];
  match_method: string;
  token_coverage: number;
  section_matches: boolean;
  raw_content?: string;
  page_start?: number;
  page_end?: number;
}

export interface EvidenceItem {
  document_key: string;
  page: number;
  section_path: string[];
  quote: string;
  resolution: {
    status: EvidenceStatus;
    match_method: string;
    resolved_chunk_ids: string[];
    candidates: Candidate[];
    reason_code: string;
  };
}

export interface ReviewQuestion {
  id: string;
  question: string;
  reference_answer: string;
  answerable: boolean;
  evidence_resolution_status: EvidenceStatus;
  evidence: EvidenceItem[];
}

export interface ReviewDataset {
  analysis_only?: boolean;
  freeze_permitted?: boolean;
  documents: { document_key: string; title: string; filename: string }[];
  dataset: ReviewQuestion[];
}

export interface ReviewReport {
  benchmark_total: number;
  answerable_total: number;
  resolved: number;
  parser_issue: number;
  manual_review: number;
  multi_chunk: number;
  unresolved: number;
  snapshot_id: string;
  run_status: string;
  freeze_permitted: boolean;
}

export type ReviewDecision = "not_reviewed" | "evidence_confirmed" | "parser_fix_needed" | "label_correction_needed" | "uncertain";

export interface ReviewNote {
  question_id: string;
  decision: ReviewDecision;
  comment: string;
}

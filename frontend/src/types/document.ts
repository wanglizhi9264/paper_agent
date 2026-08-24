export interface DocumentItem {
  id: string;
  filename: string;
  title: string | null;
  extension: string;
  status: string;
  chunk_count: number;
  page_count: number | null;
  character_count: number | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentListResponse {
  items: DocumentItem[];
  next_cursor: string | null;
}

export interface CreateDocumentResponse {
  document_id: string;
  job_id: string;
  status: string;
}

export interface JobItem {
  id: string;
  kind: string;
  status: string;
  stage: string;
  progress: number;
  attempt: number;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
}

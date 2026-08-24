export interface SessionItem {
  id: string;
  title: string;
  scope_type: string;
  created_at: string;
}

export interface SessionListResponse {
  items: SessionItem[];
  next_cursor: string | null;
}

export interface ChatSource {
  index: number;
  chunk_id: string;
  document_title: string;
  section_path: string[];
  page: string;
  content: string;
  truncated: boolean;
}

export interface ChatCitation {
  index: number;
  chunk_id: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  citations: ChatCitation[] | null;
  created_at: string;
}

export interface SessionMessagesResponse {
  items: ChatMessage[];
}

export interface SearchRequest {
  query: string;
  scope: {
    type: "all" | "documents" | "collection";
    collection_id?: string;
    document_ids?: string[];
  };
  top_k?: number;
  minimum_should_match?: number;
  debug?: boolean;
}

export interface SearchResponse {
  original_query: string;
  rewritten_query: string;
  results: SearchResultItem[];
  degraded_reasons: string[];
}

export interface SearchResultItem {
  chunk_id: string;
  score: number;
  source: string;
  rank: number;
  document_id: string;
  section_path: string[];
  raw_content: string;
  page_start: number | null;
  page_end: number | null;
}

export interface CollectionItem {
  id: string;
  name: string;
  description: string;
  document_count: number;
  created_at: string;
}

export interface CollectionListResponse {
  items: CollectionItem[];
  next_cursor: string | null;
}

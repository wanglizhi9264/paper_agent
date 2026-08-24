import { useRef, useCallback } from "react";
import { useDocuments, useUploadDocument, useDeleteDocument } from "../features/documents";
import type { DocumentItem } from "../types/document";

const STATUS_COLORS: Record<string, string> = {
  uploaded: "status-loading",
  queued: "status-loading",
  parsing: "status-loading",
  chunking: "status-loading",
  embedding: "status-loading",
  indexing: "status-loading",
  ready: "status-ok",
  failed: "status-error",
  deleting: "status-loading",
  deleted: "status-error",
};

export function DocumentsPage() {
  const { data, isLoading, isError, error, refetch } = useDocuments();
  const upload = useUploadDocument();
  const del = useDeleteDocument();
  const fileInput = useRef<HTMLInputElement>(null);

  const onFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        upload.mutate({ file });
        if (fileInput.current) fileInput.current.value = "";
      }
    },
    [upload],
  );

  return (
    <section className="page page-documents">
      <h1>Documents</h1>

      <div className="upload-box">
        <input
          ref={fileInput}
          type="file"
          accept=".pdf,.docx,.md"
          onChange={onFileChange}
          disabled={upload.isPending}
          className="file-input"
        />
        {upload.isPending && <span className="muted">Uploading…</span>}
        {upload.isError && (
          <span className="error-text">
            Upload failed: {upload.error instanceof Error ? upload.error.message : "Unknown"}
          </span>
        )}
      </div>

      {isLoading && <p className="muted">Loading documents…</p>}
      {isError && (
        <div className="error-box">
          <p>Failed to load documents.</p>
          <p className="muted">{error instanceof Error ? error.message : "Unknown error"}</p>
          <button type="button" onClick={() => void refetch()} className="btn">Retry</button>
        </div>
      )}

      {data && data.items.length === 0 && !isLoading && (
        <p className="muted">No documents yet. Upload a PDF, DOCX, or Markdown file.</p>
      )}

      {data && data.items.length > 0 && (
        <table className="doc-table">
          <thead>
            <tr>
              <th>Filename</th>
              <th>Status</th>
              <th>Chunks</th>
              <th>Pages</th>
              <th>Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((doc: DocumentItem) => (
              <tr key={doc.id}>
                <td>{doc.filename}</td>
                <td>
                  <span className={`status-dot ${STATUS_COLORS[doc.status] ?? "status-loading"}`} />
                  {doc.status}
                </td>
                <td>{doc.chunk_count}</td>
                <td>{doc.page_count ?? "—"}</td>
                <td className="muted">{new Date(doc.created_at).toLocaleString()}</td>
                <td>
                  {doc.status === "ready" && (
                    <button
                      type="button"
                      className="btn-sm btn-danger"
                      onClick={() => del.mutate(doc.id)}
                      disabled={del.isPending}
                    >
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

import { useState } from "react";
import { useCollections, useCreateCollection, useDeleteCollection } from "../features/collections";

export function CollectionsPage() {
  const { data, isLoading, isError, error, refetch } = useCollections();
  const create = useCreateCollection();
  const del = useDeleteCollection();
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (name.trim()) {
      create.mutate({ name: name.trim(), description: desc.trim() || undefined });
      setName("");
      setDesc("");
    }
  };

  return (
    <section className="page page-collections">
      <h1>Collections</h1>

      <form onSubmit={onSubmit} className="collection-form">
        <input
          type="text"
          placeholder="Collection name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="text-input"
        />
        <input
          type="text"
          placeholder="Description (optional)"
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          className="text-input"
        />
        <button type="submit" className="btn" disabled={create.isPending || !name.trim()}>
          Create
        </button>
      </form>

      {isLoading && <p className="muted">Loading collections…</p>}
      {isError && (
        <div className="error-box">
          <p>Failed to load collections.</p>
          <p className="muted">{error instanceof Error ? error.message : "Unknown"}</p>
          <button type="button" onClick={() => void refetch()} className="btn">Retry</button>
        </div>
      )}

      {data && data.items.length === 0 && !isLoading && (
        <p className="muted">No collections yet.</p>
      )}

      {data && data.items.length > 0 && (
        <div className="collection-list">
          {data.items.map((c) => (
            <div key={c.id} className="collection-card">
              <div className="collection-info">
                <span className="collection-name">{c.name}</span>
                {c.description && <span className="muted">{c.description}</span>}
                <span className="muted">{c.document_count} documents</span>
              </div>
              <button
                type="button"
                className="btn-sm btn-danger"
                onClick={() => del.mutate(c.id)}
                disabled={del.isPending}
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

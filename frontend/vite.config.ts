/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { readFile } from "node:fs/promises";
import { readdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const localReviewFiles: Record<string, string> = {
  "/dataset": join(projectRoot, "eval", "private_benchmark", "repair", "benchmark_unresolved.json"),
  "/report": join(projectRoot, "eval", "private_benchmark", "repair", "benchmark_repair_report.json"),
};
const uploadsDirectory = join(projectRoot, "storage", "uploads");

async function localPdfPaths(): Promise<Record<string, string>> {
  const dataset = JSON.parse(await readFile(localReviewFiles["/dataset"], "utf8")) as {
    documents: { document_key: string; sha256: string }[];
  };
  const byHash = new Map(dataset.documents.map((document) => [document.sha256, document.document_key]));
  const result: Record<string, string> = {};
  for (const name of await readdir(uploadsDirectory)) {
    if (!name.toLowerCase().endsWith(".pdf")) continue;
    const path = join(uploadsDirectory, name);
    const contents = await readFile(path);
    const key = byHash.get(createHash("sha256").update(contents).digest("hex"));
    if (key) result[key] = path;
  }
  return result;
}

async function localIrPage(documentKey: string, page: number): Promise<{ text: string; version_id: string; snapshot_id: string } | null> {
  if (!Number.isInteger(page) || page < 1) return null;
  const report = JSON.parse(await readFile(localReviewFiles["/report"], "utf8")) as { snapshot_id: string };
  const resolved = JSON.parse(await readFile(join(projectRoot, "eval", "private_benchmark", "dataset.resolved.json"), "utf8")) as {
    dataset: { scope: { document_keys: string[] }; runtime_scope?: { document_ids: string[] }; snapshot_labels?: { index_snapshot_id: string } }[];
  };
  if (!/^[0-9a-f-]{36}$/.test(report.snapshot_id)) return null;
  const documentIds = new Set<string>();
  for (const item of resolved.dataset) {
    if (item.snapshot_labels?.index_snapshot_id !== report.snapshot_id) return null;
    if (item.scope.document_keys.length === 1 && item.scope.document_keys[0] === documentKey &&
      item.runtime_scope?.document_ids.length === 1) documentIds.add(item.runtime_scope.document_ids[0]);
  }
  if (documentIds.size !== 1) return null;
  const documentId = [...documentIds][0];
  if (!/^[0-9a-f-]{36}$/.test(documentId)) return null;
  const manifestPath = join(projectRoot, "storage", "indexes", "versions", report.snapshot_id, "manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as { document_versions: Record<string, string> };
  const versionId = manifest.document_versions[documentId];
  if (!versionId || !/^[0-9a-f-]{36}$/.test(versionId)) return null;
  const versionDir = join(projectRoot, "storage", "ir", "versions", versionId);
  const signatures = await readdir(versionDir);
  if (signatures.length !== 1 || !/^[0-9a-f]{64}$/.test(signatures[0])) return null;
  const ir = JSON.parse(await readFile(join(versionDir, signatures[0], "document_ir.json"), "utf8")) as {
    document_id: string;
    elements: { raw_text?: string; normalized_text?: string; provenance?: { physical_page: number }[] }[];
  };
  if (ir.document_id !== documentId) return null;
  const text = ir.elements.filter((element) => element.provenance?.some((span) => span.physical_page === page))
    .map((element) => element.raw_text || element.normalized_text || "").filter(Boolean).join("\n\n");
  return { text, version_id: versionId, snapshot_id: report.snapshot_id };
}

const apiBase = process.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), {
    name: "local-private-gold-review",
    configureServer(server) {
      server.middlewares.use("/__local_gold_review", async (request, response) => {
        const path = request.url?.split("?")[0] ?? "";
        if (request.method === "GET" && path.startsWith("/ir-page/")) {
          const match = /^\/ir-page\/([a-z0-9_]+)\/([0-9]+)$/.exec(path);
          try {
            const result = match ? await localIrPage(match[1], Number(match[2])) : null;
            if (result) {
              response.writeHead(200, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" })
                .end(JSON.stringify(result));
              return;
            }
          } catch {
            // An unavailable or inconsistent active IR must never be replaced with a different version.
          }
          response.writeHead(404).end();
          return;
        }
        if (request.method === "GET" && (path === "/sources" || path.startsWith("/pdf/"))) {
          try {
            const files = await localPdfPaths();
            if (path === "/sources") {
              response.writeHead(200, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" })
                .end(JSON.stringify({ pdf_document_keys: Object.keys(files) }));
              return;
            }
            const key = path.slice("/pdf/".length);
            const file = files[key];
            if (file) {
              const body = await readFile(file);
              response.writeHead(200, { "Content-Type": "application/pdf", "Content-Length": body.byteLength,
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" }).end(body);
              return;
            }
          } catch {
            // Missing private files are reported as unavailable, never replaced by another PDF.
          }
          response.writeHead(404).end();
          return;
        }
        const file = localReviewFiles[path];
        if (request.method !== "GET" || !file) {
          response.writeHead(404).end();
          return;
        }
        try {
          const body = await readFile(file);
          response.writeHead(200, {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
          }).end(body);
        } catch {
          response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" }).end("Local private review file unavailable");
        }
      });
    },
  }],
  define: {
    "import.meta.env.VITE_API_BASE_URL": JSON.stringify(apiBase),
  },
  server: {
    port: 5173,
    host: "127.0.0.1",
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});

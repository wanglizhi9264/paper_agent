import type { ReviewDataset, ReviewReport, ReviewNote } from "../types/goldReview";

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseReviewDataset(value: unknown): ReviewDataset {
  if (!object(value) || !Array.isArray(value.dataset) || !Array.isArray(value.documents)) {
    throw new Error("所选文件不是 benchmark_unresolved.json：缺少 dataset 或 documents。 ");
  }
  for (const item of value.dataset) {
    if (!object(item) || typeof item.id !== "string" || typeof item.question !== "string" ||
      typeof item.reference_answer !== "string" || !Array.isArray(item.evidence)) {
      throw new Error("题目数据缺少 id、question、reference_answer 或 evidence。");
    }
    for (const evidence of item.evidence) {
      if (!object(evidence) || typeof evidence.document_key !== "string" ||
        typeof evidence.page !== "number" || typeof evidence.quote !== "string" ||
        !object(evidence.resolution) || !Array.isArray(evidence.resolution.candidates)) {
        throw new Error(`${item.id} 的 evidence/resolution 数据不完整。`);
      }
    }
  }
  return value as unknown as ReviewDataset;
}

export function parseReviewReport(value: unknown): ReviewReport {
  if (!object(value) || typeof value.benchmark_total !== "number" ||
    typeof value.run_status !== "string" || typeof value.snapshot_id !== "string") {
    throw new Error("所选文件不是 benchmark_repair_report.json。");
  }
  return value as unknown as ReviewReport;
}

export function parsedPageFromIr(value: unknown, page: number): string {
  if (!object(value) || !Array.isArray(value.elements)) {
    throw new Error("所选文件不是 Canonical Document IR JSON（缺少 elements）。");
  }
  const lines: string[] = [];
  for (const element of value.elements) {
    if (!object(element) || !Array.isArray(element.provenance)) continue;
    if (element.provenance.some((span) => object(span) && span.physical_page === page)) {
      if (typeof element.raw_text === "string" && element.raw_text.trim()) lines.push(element.raw_text);
      else if (typeof element.normalized_text === "string" && element.normalized_text.trim()) lines.push(element.normalized_text);
    }
  }
  return lines.join("\n\n");
}

export function createReviewExport(report: ReviewReport | null, notes: ReviewNote[]) {
  return {
    schema_version: 1,
    kind: "gold_evidence_review_notes",
    snapshot_id: report?.snapshot_id ?? null,
    generated_at: new Date().toISOString(),
    warning: "人工审视笔记；不修改 gold evidence，也不授权 benchmark freeze。",
    notes,
  };
}

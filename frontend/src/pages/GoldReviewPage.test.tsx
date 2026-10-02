import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GoldReviewPage } from "./GoldReviewPage";

const dataset = {
  analysis_only: true,
  freeze_permitted: false,
  documents: [{ document_key: "paper", title: "Paper", filename: "paper.pdf" }],
  dataset: [{
    id: "eval-001", question: "What is the result?", reference_answer: "42", answerable: true,
    evidence_resolution_status: "parser_issue",
    evidence: [{ document_key: "paper", page: 4, section_path: ["Results"], quote: "The result is 42.",
      resolution: { status: "parser_issue", match_method: "fuzzy_candidate", resolved_chunk_ids: [],
        reason_code: "SOURCE_PRESENT_PARSER_OUTPUT_MISSING", candidates: [{ chunk_ids: ["candidate-1"], match_method: "fuzzy_candidate", token_coverage: 0.8, section_matches: false }] } }],
  }],
};

describe("GoldReviewPage", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("No local fixture")));
  });

  it("shows local-file fallback when automatic loading is unavailable", async () => {
    render(<GoldReviewPage />);
    expect(await screen.findByText("本机异常题不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出审视笔记" })).toBeDisabled();
  });

  it("loads local private report automatically without a file picker", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => Promise.resolve({
      ok: !url.includes("/ir-page/"),
      json: async () => url.endsWith("/dataset") ? dataset : url.endsWith("/sources") ? { pdf_document_keys: [] } : {
        benchmark_total: 60, answerable_total: 52, resolved: 31, parser_issue: 5,
        manual_review: 16, multi_chunk: 0, unresolved: 0, snapshot_id: "snapshot",
        run_status: "analysis_only_database_unavailable", freeze_permitted: false,
      },
    })));
    render(<GoldReviewPage />);
    expect(await screen.findByRole("heading", { name: "What is the result?" })).toBeInTheDocument();
    expect(screen.getByText("未允许冻结")).toBeInTheDocument();
  });

  it("shows evidence and warns that a fuzzy candidate is not gold", async () => {
    const user = userEvent.setup();
    render(<GoldReviewPage />);
    await user.upload(screen.getByLabelText("异常题文件"), new File([JSON.stringify(dataset)], "benchmark_unresolved.json", { type: "application/json" }));
    await waitFor(() => expect(screen.getByText("The result is 42.")).toBeInTheDocument());
    expect(screen.getByText("candidate-1")).toBeInTheDocument();
    expect(screen.getByText(/不能仅凭 ID 或相似度确认/)).toBeInTheDocument();
    expect(screen.getByText(/原 PDF 中有证据/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出审视笔记" })).toBeEnabled();
  });

  it("rejects an invalid dataset instead of silently showing an empty list", async () => {
    const user = userEvent.setup();
    render(<GoldReviewPage />);
    await user.upload(screen.getByLabelText("异常题文件"), new File(["{}"], "bad.json", { type: "application/json" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("缺少 dataset 或 documents");
  });
});

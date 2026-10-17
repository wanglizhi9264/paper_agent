import { describe, expect, it } from "vitest";
import { parseAgentEvent } from "../types/agent";
import { terminal } from "./agent";

describe("Agent event contract", () => {
  it("accepts persisted events and rejects malformed sequences", () => {
    expect(parseAgentEvent('{"sequence":2,"type":"answer.delta","payload":{"text":"hi"}}')).toEqual({ sequence: 2, type: "answer.delta", payload: { text: "hi" } });
    expect(parseAgentEvent('{"sequence":"2","type":"answer.delta","payload":{}}')).toBeNull();
    expect(parseAgentEvent('{"sequence":2,"type":"answer.delta","payload":null}')).toBeNull();
  });
  it("keeps interrupted runs resumable", () => {
    expect(terminal("interrupted")).toBe(false);
    expect(terminal("succeeded")).toBe(true);
    expect(terminal("failed")).toBe(true);
    expect(terminal("cancelled")).toBe(true);
  });
});

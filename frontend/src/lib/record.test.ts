import { describe, expect, it } from "vitest";

import { parseExport } from "./record";

describe("parseExport", () => {
  it("parses user and llm sets", () => {
    const n = '{"text":"nausea","start":15,"end":21,"n":1.0E2}';
    const body =
      '{"case_id":"c","username":"u","submitted_at":"s","updated_at":"t","version":2,' +
      '"record":{"narrative":"Aspirin caused nausea.","annotation_sets":{' +
      '"llm:b:m2":{"kind":"llm","provider":"b","model":"m2","generated_at":"g2",' +
      '"annotations":{"AE":[{"text":"nausea","start":15,"end":21}]}},' +
      `"user":{"kind":"user","annotations":{"AE":[${n}]}},` +
      '"llm:a:m1":{"kind":"llm","provider":"a","model":"m1","generated_at":"g1","annotations":{}}}}}';
    const loaded = parseExport(body);
    expect(loaded.userAnnotations).toHaveLength(1);
    expect(loaded.userAnnotations[0]?.source).toBe(n);
    expect(loaded.llmSets.map((s) => s.id)).toEqual(["llm:a:m1", "llm:b:m2"]);
    const second = loaded.llmSets[1];
    expect(second).toMatchObject({ provider: "b", model: "m2", generatedAt: "g2" });
    expect(second?.annotations.map((a) => [a.label, a.text])).toEqual([["AE", "nausea"]]);
    expect(loaded.llmSets[0]?.annotations).toEqual([]);
  });

  it("parses a record without annotation_sets", () => {
    const loaded = parseExport('{"version":1,"record":{"narrative":"n"}}');
    expect(loaded.userAnnotations).toEqual([]);
    expect(loaded.llmSets).toEqual([]);
  });
});

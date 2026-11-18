import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, describeError, discardProposal } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function altered(alteration?: string): Response {
  const issue = { location: "llm_output", path: "", code: "NARRATIVE_ALTERED", message: "x" };
  const body = {
    error: { code: "LLM_OUTPUT_INVALID", message: "x" },
    issues: [alteration === undefined ? issue : { ...issue, alteration, first_difference: 12 }],
  };
  return new Response(JSON.stringify(body), {
    status: 502,
    headers: { "Content-Type": "application/json" },
  });
}

async function rejection(response: Response): Promise<unknown> {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response)));
  return discardProposal("1-1", "u", "p").then(
    () => null,
    (error: unknown) => error,
  );
}

describe("altered narrative failures", () => {
  it("reads the alteration from the issue", async () => {
    const error = await rejection(altered("WRAPPER_TEXT"));
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).reason).toBe("NARRATIVE_ALTERED");
    expect((error as ApiError).alteration).toBe("WRAPPER_TEXT");
  });

  it("has no alteration when the issue has none", async () => {
    const error = await rejection(altered());
    expect((error as ApiError).alteration).toBeNull();
  });

  it("explains the kind of change", () => {
    expect(
      describeError(new ApiError(502, "LLM_OUTPUT_INVALID", "NARRATIVE_ALTERED", "TEXT_OMITTED")),
    ).toBe(
      "The model changed the narrative text, so its annotations can't be used. It left out part of the narrative. Running it again may help.",
    );
  });

  it.each([
    ["UNKNOWN_TAG", "It used tags this service doesn't recognize."],
    ["WRAPPER_TEXT", "It added text before or after the narrative."],
    ["LINE_ENDINGS", "It changed the line endings."],
    ["WHITESPACE", "It changed spaces or line breaks."],
    ["ENTITY_ESCAPED", "It escaped characters such as & or <."],
    ["CHARACTERS_NORMALIZED", "It replaced quotes, dashes, or accented characters."],
    ["TEXT_OMITTED", "It left out part of the narrative."],
    ["TEXT_ADDED", "It added text inside the narrative."],
    ["TEXT_CHANGED", "It reworded part of the narrative."],
  ])("describes %s", (alteration, sentence) => {
    expect(
      describeError(new ApiError(502, "LLM_OUTPUT_INVALID", "NARRATIVE_ALTERED", alteration)),
    ).toContain(` ${sentence} `);
  });

  it("keeps the general message for an unknown or missing alteration", () => {
    const general =
      "The model changed the narrative text, so its annotations can't be used. Running it again may help.";
    expect(describeError(new ApiError(502, "LLM_OUTPUT_INVALID", "NARRATIVE_ALTERED"))).toBe(general);
    expect(
      describeError(new ApiError(502, "LLM_OUTPUT_INVALID", "NARRATIVE_ALTERED", "SOMETHING_NEW")),
    ).toBe(general);
  });
});

import { describe, expect, it } from "vitest";

import { NarrativeIndex } from "./offsets";

describe("NarrativeIndex (F08)", () => {
  it("counts an astral character as one code point", () => {
    const index = new NarrativeIndex("a😀b");
    expect(index.length).toBe(3);
    expect(index.toUnit(2)).toBe(3);
    expect(index.toCodePoint(3)).toBe(2);
    expect(index.slice(1, 2)).toBe("😀");
    expect(index.slice(2, 3)).toBe("b");
  });

  it("counts CRLF as two code points", () => {
    const index = new NarrativeIndex("a\r\nb");
    expect(index.length).toBe(4);
    expect(index.slice(1, 3)).toBe("\r\n");
  });

  it("counts a combining mark separately", () => {
    const index = new NarrativeIndex("e\u0301x");
    expect(index.length).toBe(3);
    expect(index.slice(0, 1)).toBe("e");
    expect(index.slice(1, 2)).toBe("\u0301");
  });

  it("rounds a position inside a surrogate pair down", () => {
    const index = new NarrativeIndex("😀😀");
    expect(index.toCodePoint(1)).toBe(0);
    expect(index.toCodePoint(2)).toBe(1);
    expect(index.toCodePoint(4)).toBe(2);
  });

  it("maps both ends of an empty narrative", () => {
    const index = new NarrativeIndex("");
    expect(index.length).toBe(0);
    expect(index.toUnit(0)).toBe(0);
    expect(index.toCodePoint(0)).toBe(0);
  });

  it("rejects offsets outside the narrative", () => {
    const index = new NarrativeIndex("ab");
    expect(() => index.toUnit(3)).toThrow(RangeError);
    expect(() => index.toCodePoint(3)).toThrow(RangeError);
  });

  it("agrees with the server's example offsets", () => {
    const index = new NarrativeIndex("Aspirin caused nausea.");
    expect(index.slice(0, 7)).toBe("Aspirin");
    expect(index.slice(15, 21)).toBe("nausea");
  });
});

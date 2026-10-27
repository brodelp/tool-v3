import { describe, expect, it } from "vitest";

import { canBreakAt, pageOf, paginate, type Fits } from "./paginate";

/** A fake layout: at most `n` UTF-16 units per page, counting every unit. */
const atMost =
  (n: number): Fits =>
  (start, end) =>
    end - start <= n;

function pages(text: string, fits: Fits): string[] {
  const starts = paginate(text, fits);
  return starts.map((s, k) => text.slice(s, starts[k + 1] ?? text.length));
}

describe("paginate (F09)", () => {
  it("keeps a fitting narrative on one page", () => {
    expect(paginate("short", atMost(10))).toEqual([0]);
    expect(paginate("", atMost(10))).toEqual([0]);
  });

  it("breaks after whitespace and loses nothing", () => {
    const text = "alpha beta gamma delta epsilon";
    const result = pages(text, atMost(12));
    expect(result).toEqual(["alpha beta ", "gamma delta ", "epsilon"]);
    expect(result.join("")).toBe(text);
  });

  it("breaks inside a word only when a word is longer than a page", () => {
    expect(pages("abcdefghij", atMost(4))).toEqual(["abcd", "efgh", "ij"]);
  });

  it("never splits a surrogate pair", () => {
    const text = "😀😀😀"; // six UTF-16 units
    const result = pages(text, atMost(3));
    expect(result).toEqual(["😀", "😀", "😀"]);
  });

  it("gives an astral character wider than a page a page of its own", () => {
    expect(pages("😀a", atMost(1))).toEqual(["😀", "a"]);
  });

  it("never splits CRLF", () => {
    const text = "aaaa\r\nbbbb";
    const result = pages(text, atMost(5));
    expect(result.join("")).toBe(text);
    for (const page of result) {
      expect(page.endsWith("\r")).toBe(false);
      expect(page.startsWith("\n")).toBe(false);
    }
  });

  it("treats a no-break space as part of the word", () => {
    expect(pages("aa\u00a0bb cc", atMost(6))).toEqual(["aa\u00a0bb ", "cc"]);
  });

  it("uses few measurements for long narratives", () => {
    let calls = 0;
    const counted: Fits = (s, e) => {
      calls++;
      return e - s <= 500;
    };
    const text = "word ".repeat(20000);
    const starts = paginate(text, counted);
    expect(starts.length).toBe(200);
    expect(calls / starts.length).toBeLessThan(15);
  });

  it("re-paginates to different breaks for a different area", () => {
    const text = "one two three four five six";
    expect(paginate(text, atMost(10))).not.toEqual(paginate(text, atMost(20)));
  });
});

describe("pageOf", () => {
  it("finds the page holding a position", () => {
    const starts = [0, 10, 20];
    expect(pageOf(starts, 0)).toBe(0);
    expect(pageOf(starts, 10)).toBe(1);
    expect(pageOf(starts, 25)).toBe(2);
  });
});

describe("canBreakAt", () => {
  it("rejects the middle of a pair and of CRLF", () => {
    expect(canBreakAt("😀", 1)).toBe(false);
    expect(canBreakAt("\r\n", 1)).toBe(false);
    expect(canBreakAt("ab", 1)).toBe(true);
  });
});

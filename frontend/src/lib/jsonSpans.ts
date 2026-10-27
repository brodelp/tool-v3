/**
 * Locates values inside JSON text without re-serializing it, so exact source text can be kept.
 * The input is a document the server already validated; malformed input throws.
 */

const WHITESPACE = new Set([" ", "\t", "\n", "\r"]);

export interface Span {
  readonly start: number;
  readonly end: number;
}

export interface MemberSpan extends Span {
  readonly name: string;
}

function skipWhitespace(text: string, i: number): number {
  while (WHITESPACE.has(text.charAt(i))) i++;
  return i;
}

function skipString(text: string, i: number): number {
  for (let j = i + 1; j < text.length; j++) {
    const c = text.charAt(j);
    if (c === "\\") j++;
    else if (c === '"') return j + 1;
  }
  throw new SyntaxError("unterminated JSON string");
}

/** The end of the JSON value starting at `i`. */
export function skipValue(text: string, i: number): number {
  const c = text.charAt(i);
  if (c === '"') return skipString(text, i);
  if (c !== "{" && c !== "[") {
    let j = i;
    while (j < text.length && !",}] \t\n\r".includes(text.charAt(j))) j++;
    if (j === i) throw new SyntaxError("expected a JSON value");
    return j;
  }
  let depth = 0;
  for (let j = i; j < text.length; j++) {
    const d = text.charAt(j);
    if (d === '"') j = skipString(text, j) - 1;
    else if (d === "{" || d === "[") depth++;
    else if (d === "}" || d === "]") {
      depth--;
      if (depth === 0) return j + 1;
    }
  }
  throw new SyntaxError("unterminated JSON container");
}

/** The members of the object starting at `i`, with their decoded names and value spans. */
export function objectMembers(text: string, i: number): MemberSpan[] {
  if (text.charAt(i) !== "{") throw new SyntaxError("expected a JSON object");
  const members: MemberSpan[] = [];
  let j = skipWhitespace(text, i + 1);
  if (text.charAt(j) === "}") return members;
  for (;;) {
    const keyEnd = skipString(text, j);
    const name = JSON.parse(text.slice(j, keyEnd)) as string;
    const start = skipWhitespace(text, skipWhitespace(text, keyEnd) + 1); // past ':'
    const end = skipValue(text, start);
    members.push({ name, start, end });
    j = skipWhitespace(text, end);
    if (text.charAt(j) === "}") return members;
    j = skipWhitespace(text, j + 1); // past ','
  }
}

/** The element spans of the array starting at `i`. */
export function arrayElements(text: string, i: number): Span[] {
  if (text.charAt(i) !== "[") throw new SyntaxError("expected a JSON array");
  const elements: Span[] = [];
  let j = skipWhitespace(text, i + 1);
  if (text.charAt(j) === "]") return elements;
  for (;;) {
    const end = skipValue(text, j);
    elements.push({ start: j, end });
    j = skipWhitespace(text, end);
    if (text.charAt(j) === "]") return elements;
    j = skipWhitespace(text, j + 1);
  }
}

export function member(members: readonly MemberSpan[], name: string): MemberSpan | undefined {
  return members.find((m) => m.name === name);
}

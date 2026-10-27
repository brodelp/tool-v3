/**
 * Splits a narrative into pages that fit the reading area (stage 3 §4.2). Positions are UTF-16
 * units of the narrative string. `fits(start, end)` reports whether text.slice(start, end) fits
 * one page; it must be monotonic (a fitting slice's prefixes fit too).
 */
export type Fits = (start: number, end: number) => boolean;

// Break opportunities in `white-space: pre-wrap`. No-break spaces (U+00A0, U+2007, U+202F) and
// U+FEFF are not among them.
const BREAKABLE = /[\t\n\v\f\r \u1680\u2000-\u2006\u2008-\u200a\u2028\u2029\u205f\u3000]/;

function isHigh(code: number): boolean {
  return code >= 0xd800 && code <= 0xdbff;
}

function isLow(code: number): boolean {
  return code >= 0xdc00 && code <= 0xdfff;
}

/** Whether a page may end at `i`: never inside a surrogate pair or between CR and LF. */
export function canBreakAt(text: string, i: number): boolean {
  if (i <= 0 || i >= text.length) return true;
  if (isHigh(text.charCodeAt(i - 1)) && isLow(text.charCodeAt(i))) return false;
  return !(text.charAt(i - 1) === "\r" && text.charAt(i) === "\n");
}

/** The largest end in (start, text.length] for which the slice fits; at least one unit. */
function largestFit(text: string, start: number, fits: Fits, guess: number): number {
  const limit = text.length;
  let lo = start + 1;
  if (!fits(start, lo)) return lo;
  let hi = Math.min(limit, start + Math.max(guess, 64));
  // Gallop from the previous page's length, then bisect: few measurements per page.
  while (hi < limit && fits(start, hi)) {
    lo = hi;
    hi = Math.min(limit, start + (hi - start) * 2);
  }
  if (fits(start, hi)) return hi;
  while (hi - lo > 1) {
    const mid = lo + Math.floor((hi - lo) / 2);
    if (fits(start, mid)) lo = mid;
    else hi = mid;
  }
  return lo;
}

/** Prefer ending after whitespace; otherwise end between characters at the last legal point. */
function chooseBreak(text: string, start: number, maxEnd: number): number {
  if (maxEnd >= text.length) return text.length;
  for (let i = maxEnd; i > start; i--) {
    if (BREAKABLE.test(text.charAt(i - 1)) && canBreakAt(text, i)) return i;
  }
  for (let i = maxEnd; i > start; i--) {
    if (canBreakAt(text, i)) return i;
  }
  // A single unbreakable unit wider than the page still has to go somewhere.
  let i = maxEnd + 1;
  while (!canBreakAt(text, i)) i++;
  return i;
}

/** Start positions of each page; page k is [starts[k], starts[k+1] ?? text.length). */
export function paginate(text: string, fits: Fits): number[] {
  const starts = [0];
  let start = 0;
  let guess = 0;
  // Never measure the whole remaining text per page: that would be quadratic for long narratives.
  while (start < text.length) {
    const end = chooseBreak(text, start, largestFit(text, start, fits, guess));
    guess = end - start;
    start = end;
    if (start < text.length) starts.push(start);
  }
  return starts;
}

/** The page containing UTF-16 position `at`. */
export function pageOf(starts: readonly number[], at: number): number {
  let page = 0;
  for (let k = 0; k < starts.length; k++) {
    if ((starts[k] ?? 0) <= at) page = k;
  }
  return page;
}

/**
 * Unicode code points (the API's offset unit, stage 1 §3.3) versus UTF-16 code units (what the
 * browser's strings, DOM ranges, and selections count). An astral character such as an emoji is
 * one code point but two code units; CRLF is two of each.
 */
export class NarrativeIndex {
  readonly text: string;
  /** Length in code points. */
  readonly length: number;
  private readonly cpToUnit: Uint32Array;
  private readonly unitToCp: Uint32Array;

  constructor(text: string) {
    this.text = text;
    const cpToUnit: number[] = [];
    const unitToCp = new Uint32Array(text.length + 1);
    let cp = 0;
    let unit = 0;
    for (const char of text) {
      cpToUnit.push(unit);
      for (let k = 0; k < char.length; k++) unitToCp[unit + k] = cp;
      unit += char.length;
      cp += 1;
    }
    cpToUnit.push(unit);
    unitToCp[unit] = cp;
    this.length = cp;
    this.cpToUnit = Uint32Array.from(cpToUnit);
    this.unitToCp = unitToCp;
  }

  /** The UTF-16 position of a code-point offset (0 ≤ cp ≤ length). */
  toUnit(cp: number): number {
    const unit = this.cpToUnit[cp];
    if (unit === undefined) throw new RangeError("code-point offset out of range");
    return unit;
  }

  /**
   * The code-point offset of a UTF-16 position. A position inside a surrogate pair (which a
   * selection should never produce) rounds down to the start of that character.
   */
  toCodePoint(unit: number): number {
    const cp = this.unitToCp[unit];
    if (cp === undefined) throw new RangeError("UTF-16 position out of range");
    return cp;
  }

  /** The exact narrative slice at code-point offsets [start, end). */
  slice(start: number, end: number): string {
    return this.text.slice(this.toUnit(start), this.toUnit(end));
  }
}

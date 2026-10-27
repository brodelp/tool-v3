import { LABEL_INDEX, type Annotation } from "./annotations";

/** A run of narrative text covered by the same set of annotations. Offsets are code points. */
export interface Segment {
  readonly start: number;
  readonly end: number;
  /** Covering annotations, outermost first. */
  readonly covering: readonly Annotation[];
  /** Whether the pending (not yet labelled) selection covers this run. */
  readonly pending: boolean;
}

export interface Range {
  readonly start: number;
  readonly end: number;
}

/**
 * Splits [start, end) at every annotation boundary, so nested, crossing, and adjacent spans
 * render as flat runs and never nest DOM elements.
 */
export function segment(
  start: number,
  end: number,
  annotations: readonly Annotation[],
  pending: Range | null = null,
): Segment[] {
  const cuts = new Set([start, end]);
  const ranges: Range[] = pending ? [...annotations, pending] : [...annotations];
  for (const r of ranges) {
    if (r.start > start && r.start < end) cuts.add(r.start);
    if (r.end > start && r.end < end) cuts.add(r.end);
  }
  const points = [...cuts].sort((a, b) => a - b);
  const segments: Segment[] = [];
  for (let i = 0; i + 1 < points.length; i++) {
    const s = points[i] ?? 0;
    const e = points[i + 1] ?? 0;
    const covering = annotations
      .filter((a) => a.start < e && a.end > s)
      .sort((a, b) => b.end - b.start - (a.end - a.start));
    const inPending = pending !== null && pending.start < e && pending.end > s;
    segments.push({ start: s, end: e, covering, pending: inPending });
  }
  return segments;
}

/** Annotations covering a code-point position (the character starting there), innermost first. */
export function coveringAt(annotations: readonly Annotation[], at: number): Annotation[] {
  return annotations
    .filter((a) => a.start <= at && at < a.end)
    .sort(
      (a, b) =>
        a.end - a.start - (b.end - b.start) ||
        (LABEL_INDEX.get(a.label) ?? 0) - (LABEL_INDEX.get(b.label) ?? 0),
    );
}

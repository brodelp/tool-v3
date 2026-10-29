import { LABELS, type Label } from "../generated/labels";
import type { NarrativeIndex } from "./offsets";

/** One annotation. Offsets are code points, as stored by the API. */
export interface Annotation {
  readonly id: string;
  readonly label: Label;
  readonly start: number;
  readonly end: number;
  /** Always the exact narrative slice at [start, end). */
  readonly text: string;
  /**
   * The exact JSON text of an annotation loaded from the server. It is sent back unchanged, so
   * foreign properties, their number tokens, escapes, and key order survive a save (stage 3 §4.4).
   */
  readonly source?: string;
  /** Serialization order within a label: loaded first, as loaded; then new ones by position. */
  readonly order: readonly number[];
}

export const LABEL_INDEX: ReadonlyMap<string, number> = new Map(LABELS.map((l, i) => [l, i]));

export function isLabel(value: string): value is Label {
  return LABEL_INDEX.has(value);
}

let sequence = 0;

function nextId(): string {
  sequence += 1;
  return `a${sequence}`;
}

/** An annotation from the server; `source` is its exact JSON text, when it was kept. */
export function loadedAnnotation(
  label: Label,
  start: number,
  end: number,
  text: string,
  source: string | undefined,
  position: number,
): Annotation {
  const order = [0, LABEL_INDEX.get(label) ?? 0, position];
  const annotation = { id: nextId(), label, start, end, text, order };
  return source === undefined ? annotation : { ...annotation, source };
}

export function newAnnotation(
  index: NarrativeIndex,
  label: Label,
  start: number,
  end: number,
): Annotation {
  if (!(start >= 0 && start < end && end <= index.length)) {
    throw new RangeError("annotation range must satisfy 0 <= start < end <= length");
  }
  const id = nextId();
  return { id, label, start, end, text: index.slice(start, end), order: [1, start, end, sequence] };
}

/** Stage 1 rejects a repeated (label, text, start, end); text follows from the range here. */
export function isDuplicate(
  annotations: readonly Annotation[],
  label: Label,
  start: number,
  end: number,
  ignoreId?: string,
): boolean {
  return annotations.some(
    (a) => a.id !== ignoreId && a.label === label && a.start === start && a.end === end,
  );
}

function compareOrder(a: readonly number[], b: readonly number[]): number {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] ?? -1) - (b[i] ?? -1);
    if (d !== 0) return d;
  }
  return 0;
}

/** Narrative order: start, then longer spans first, then canonical label order. */
export function byPosition(a: Annotation, b: Annotation): number {
  return (
    a.start - b.start ||
    b.end - a.end ||
    (LABEL_INDEX.get(a.label) ?? 0) - (LABEL_INDEX.get(b.label) ?? 0)
  );
}

export interface SerializedMap {
  /** The annotation map as JSON text, labels in canonical order. */
  readonly text: string;
  /** Annotation id at each `label/index` position of the map, to place report issues. */
  readonly positions: ReadonlyMap<string, string>;
}

export function serializeMap(annotations: readonly Annotation[]): SerializedMap {
  const positions = new Map<string, string>();
  const members: string[] = [];
  for (const label of LABELS) {
    const items = annotations
      .filter((a) => a.label === label)
      .sort((a, b) => compareOrder(a.order, b.order));
    if (items.length === 0) continue;
    items.forEach((a, i) => positions.set(`${label}/${i}`, a.id));
    const values = items.map(
      (a) => a.source ?? JSON.stringify({ text: a.text, start: a.start, end: a.end }),
    );
    members.push(`${JSON.stringify(label)}:[${values.join(",")}]`);
  }
  return { text: `{${members.join(",")}}`, positions };
}

/** Lanes for stacked highlight stripes: overlapping annotations never share a lane. */
export function assignLanes(annotations: readonly Annotation[]): ReadonlyMap<string, number> {
  const lanes = new Map<string, number>();
  const laneEnds: number[] = [];
  for (const a of [...annotations].sort(byPosition)) {
    let lane = laneEnds.findIndex((end) => end <= a.start);
    if (lane === -1) lane = laneEnds.length;
    laneEnds[lane] = a.end;
    lanes.set(a.id, lane);
  }
  return lanes;
}

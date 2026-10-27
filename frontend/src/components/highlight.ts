import type { CSSProperties } from "react";

import { labelColor } from "../lib/colors";
import type { Segment } from "../lib/segments";

/** Stripe height and how many lanes are drawn; deeper overlaps show in the inspector. */
export const LANE_PX = 3;
export const MAX_LANES = 3;

const PENDING_STRIPE = "repeating-linear-gradient(90deg, var(--pen) 0 5px, transparent 5px 8px)";

/**
 * Styles one run of text: every covering annotation draws a stripe in its own lane (so nested and
 * crossing spans stay distinguishable), and the innermost one tints the text. Backgrounds only,
 * so highlighting never changes line layout and pagination stays exact.
 */
export function segmentStyle(
  seg: Segment,
  lanes: ReadonlyMap<string, number>,
  selectedId: string | null,
): CSSProperties {
  const images: string[] = [];
  const positions: string[] = [];
  const sizes: string[] = [];
  if (seg.pending) {
    images.push(PENDING_STRIPE);
    positions.push("0 100%");
    sizes.push(`100% ${LANE_PX}px`);
  }
  for (const a of seg.covering) {
    const lane = lanes.get(a.id) ?? 0;
    if (lane >= MAX_LANES) continue;
    const { ink } = labelColor(a.label);
    images.push(`linear-gradient(${ink}, ${ink})`);
    positions.push(`0 calc(100% - ${lane * LANE_PX}px)`);
    sizes.push(`100% ${a.id === selectedId ? LANE_PX + 1 : LANE_PX}px`);
  }
  const selected = seg.covering.find((a) => a.id === selectedId);
  const inner = selected ?? seg.covering[seg.covering.length - 1];
  let fill = "transparent";
  if (seg.pending) fill = "var(--pen-wash)";
  else if (inner !== undefined) {
    const color = labelColor(inner.label);
    fill = selected === undefined ? color.wash : color.strong;
  }
  return {
    backgroundColor: fill,
    backgroundImage: images.join(", "),
    backgroundPosition: positions.join(", "),
    backgroundSize: sizes.join(", "),
    backgroundRepeat: "no-repeat",
  };
}

import type { CSSProperties } from "react";

import type { Label } from "../generated/labels";
import { LABEL_INDEX } from "./annotations";

export interface LabelColor {
  /** Stripe and swatch colour. */
  readonly ink: string;
  /** Translucent fill behind the text. */
  readonly wash: string;
  /** Stronger fill for the selected annotation. */
  readonly strong: string;
}

// Golden-angle hue steps keep neighbouring labels far apart; alternating lightness separates
// labels whose hues come around close together. Colours carry no meaning (stage 1 §3.2).
const GOLDEN_ANGLE = 137.508;

export function labelColor(label: Label): LabelColor {
  const i = LABEL_INDEX.get(label) ?? 0;
  const hue = (i * GOLDEN_ANGLE + 20) % 360;
  const lightness = i % 2 === 0 ? 0.6 : 0.7;
  const base = `${lightness} 0.16 ${hue.toFixed(1)}`;
  return {
    ink: `oklch(${base})`,
    wash: `oklch(${base} / 0.2)`,
    strong: `oklch(${base} / 0.42)`,
  };
}

/** Exposes a label's colour to CSS as `--label-ink` (set through CSSOM, so the CSP allows it). */
export function inkStyle(label: Label): CSSProperties {
  return { "--label-ink": labelColor(label).ink } as CSSProperties;
}

import { useLayoutEffect, useState } from "react";

import { paginate, type Fits } from "../lib/paginate";

export interface Measurer {
  readonly fits: Fits;
  dispose(): void;
}

/** Builds a `fits` test for one layout pass of `text` in the page body `area`. */
export type MeasurerFactory = (area: HTMLElement, text: string) => Measurer;

/**
 * Measures with a hidden box styled like the page text (`.page-measure`), so a page holds what
 * really fits at the current width, height, and font size. It is a sibling of the text root,
 * never inside it, so selection offsets only ever count narrative text.
 */
export const domMeasurer: MeasurerFactory = (area, text) => {
  const box = document.createElement("div");
  box.className = "page-measure";
  box.setAttribute("aria-hidden", "true");
  area.appendChild(box);
  return {
    fits: (start, end) => {
      box.textContent = text.slice(start, end);
      return box.scrollHeight <= box.clientHeight;
    },
    dispose: () => {
      box.remove();
    },
  };
};

// Below this the area is collapsed or hidden; measuring it would give one character per page.
const MIN_AREA_PX = 40;

/** Page start positions (UTF-16) for `text` in `area`, recomputed when the area resizes. */
export function usePages(
  text: string,
  area: HTMLElement | null,
  measurer: MeasurerFactory = domMeasurer,
): readonly number[] {
  const [starts, setStarts] = useState<readonly number[]>([0]);

  useLayoutEffect(() => {
    if (area === null) return;
    let cancelled = false;
    const run = () => {
      if (cancelled) return;
      if (measurer === domMeasurer && (area.clientHeight < MIN_AREA_PX || area.clientWidth < MIN_AREA_PX)) {
        setStarts([0]);
        return;
      }
      const measure = measurer(area, text);
      try {
        const next = paginate(text, measure.fits);
        setStarts((previous) =>
          previous.length === next.length && previous.every((s, k) => s === next[k])
            ? previous
            : next,
        );
      } finally {
        measure.dispose();
      }
    };
    run();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(run);
    observer?.observe(area);
    // Web fonts change glyph widths once loaded (jsdom has no FontFaceSet).
    if ("fonts" in document) void document.fonts.ready.then(run);
    return () => {
      cancelled = true;
      observer?.disconnect();
    };
  }, [text, area, measurer]);

  return starts;
}

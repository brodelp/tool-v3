/**
 * Maps a DOM selection inside a page's text root to UTF-16 offsets within that page. The root
 * must contain only narrative text nodes (decoration is CSS-only), so a Range's text length from
 * the root's start is exactly the offset.
 */
export function offsetWithin(root: Node, node: Node, offset: number): number {
  const range = document.createRange();
  range.setStart(root, 0);
  range.setEnd(node, offset);
  return range.toString().length;
}

export interface PageSelection {
  readonly start: number;
  readonly end: number;
  readonly collapsed: boolean;
}

/** The current selection as page-relative offsets, or `null` when it is outside `root`. */
export function readSelection(root: HTMLElement): PageSelection | null {
  const selection = window.getSelection();
  if (selection === null || selection.rangeCount === 0) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const start = offsetWithin(root, range.startContainer, range.startOffset);
  const end = offsetWithin(root, range.endContainer, range.endOffset);
  return { start, end, collapsed: range.collapsed };
}

export function clearSelection(): void {
  window.getSelection()?.removeAllRanges();
}

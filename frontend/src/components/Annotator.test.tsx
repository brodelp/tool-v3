import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MeasurerFactory } from "../hooks/usePages";
import { parseExport } from "../lib/record";
import { Annotator } from "./Annotator";

/** Fake layout: a page holds at most `n` UTF-16 units. */
const pagesOf =
  (n: number): MeasurerFactory =>
  () => ({ fits: (s, e) => e - s <= n, dispose: () => undefined });

function exportBody(narrative: string, annotations = "{}", version = 1): string {
  return JSON.stringify({
    case_id: "5001-1",
    username: "demo_annotator",
    submitted_at: "2026-10-23T14:05:09.123456Z",
    updated_at: "2026-10-23T14:05:09.123456Z",
    version,
    offset_unit: "unicode-code-point",
    record: { case_id: "5001-1", username: "demo_annotator", narrative, __map__: 0 },
  }).replace('"__map__":0', `"annotation_sets":{"user":{"kind":"user","annotations":${annotations}}}`);
}

function setup(narrative: string, annotations = "{}", measurer = pagesOf(10_000)) {
  const onReload = vi.fn();
  const view = render(
    <Annotator
      submission={parseExport(exportBody(narrative, annotations))}
      onBack={vi.fn()}
      onReload={onReload}
      measurer={measurer}
    />,
  );
  const text = view.container.querySelector<HTMLElement>(".page-text");
  if (text === null) throw new Error("no page text");
  return { ...view, text, onReload };
}

/** Selects UTF-16 positions [start, end) of the page text, as a mouse drag would. */
function select(root: HTMLElement, start: number, end: number): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let seen = 0;
  let startSet = false;
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    const length = node.textContent?.length ?? 0;
    if (!startSet && start <= seen + length) {
      range.setStart(node, start - seen);
      startSet = true;
    }
    if (end <= seen + length) {
      range.setEnd(node, end - seen);
      break;
    }
    seen += length;
  }
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
}

function mouseUp(root: HTMLElement, init: MouseEventInit = {}): void {
  fireEvent.mouseUp(root, { clientX: 100, clientY: 100, ...init });
}

function chooseLabel(label: string): void {
  fireEvent.click(screen.getByRole("option", { name: label }));
}

type FetchArgs = [string, RequestInit | undefined];

function stubFetch(...responses: Response[]) {
  const fetchMock = vi.fn<(...args: FetchArgs) => Promise<Response>>();
  for (const response of responses) fetchMock.mockResolvedValueOnce(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

const consoleSpies: ReturnType<typeof vi.spyOn>[] = [];

beforeEach(() => {
  for (const method of ["log", "info", "warn", "error", "debug"] as const) {
    consoleSpies.push(vi.spyOn(console, method));
  }
});

afterEach(() => {
  // F14: nothing (narratives, annotations, usernames) is ever written to the console.
  for (const spy of consoleSpies.splice(0)) expect(spy).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});

describe("creating annotations (F10)", () => {
  it("annotates exactly the selected range and saves it", async () => {
    const { text } = setup("Aspirin caused nausea.");
    select(text, 15, 21);
    mouseUp(text);
    const picker = screen.getByRole("dialog", { name: "Choose a label" });
    expect(within(picker).getByText("nausea")).toBeInTheDocument();
    chooseLabel("AE");
    expect(screen.queryByRole("dialog")).toBeNull();
    const mark = text.querySelector(".mark");
    expect(mark?.textContent).toBe("nausea");
    expect(screen.getByRole("status")).toHaveTextContent("Unsaved changes");

    const fetchMock = stubFetch(
      json(200, { version: 2, updated_at: "2026-10-27T09:12:44.000001Z" }, { ETag: '"2"' }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/v1/submissions/5001-1/annotation-sets/user?username=demo_annotator");
    expect(init?.method).toBe("PUT");
    expect(init?.headers).toMatchObject({ "If-Match": '"1"' });
    expect(init?.body).toBe('{"annotations":{"AE":[{"text":"nausea","start":15,"end":21}]}}');
    expect(screen.getByText("Version 2")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/^Saved at/);
  });

  it("filters labels by typing and picks with Enter", async () => {
    const { text } = setup("Aspirin caused nausea.");
    select(text, 0, 7);
    mouseUp(text);
    await userEvent.keyboard("sd{Enter}");
    expect(screen.getByRole("heading", { name: /SDrug/ })).toBeInTheDocument();
  });

  it("keeps selections exactly as made, spaces included", () => {
    const { text } = setup("Aspirin caused nausea.");
    select(text, 7, 15);
    mouseUp(text);
    chooseLabel("Dose");
    expect(text.querySelector(".mark")?.textContent).toBe(" caused ");
  });

  it("uses code points for offsets after astral characters", async () => {
    const { text } = setup("😀 rash");
    select(text, 3, 7); // UTF-16 positions of "rash"
    mouseUp(text);
    chooseLabel("AE");
    const fetchMock = stubFetch(json(200, { version: 2, updated_at: "2026-10-27T09:00:00Z" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(
      '{"annotations":{"AE":[{"text":"rash","start":2,"end":6}]}}',
    );
  });

  it("refuses an exact duplicate", () => {
    const { text } = setup("Aspirin caused nausea.", '{"AE":[{"text":"nausea","start":15,"end":21}]}');
    select(text, 15, 21);
    mouseUp(text);
    chooseLabel("AE");
    expect(screen.getByRole("alert")).toHaveTextContent("This text already has the AE label.");
    expect(screen.getByRole("dialog", { name: "Choose a label" })).toBeInTheDocument();
  });

  it("cancels a pending selection with Escape", async () => {
    const { text } = setup("Aspirin caused nausea.");
    select(text, 0, 7);
    mouseUp(text);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(text.querySelector(".mark")).toBeNull();
  });
});

describe("pages (F09, F10)", () => {
  const narrative = "alpha beta gamma delta epsilon";

  it("splits the narrative and pages with buttons and arrow keys", async () => {
    const { text } = setup(narrative, "{}", pagesOf(12));
    expect(text.textContent).toBe("alpha beta ");
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(text.textContent).toBe("gamma delta ");
    await userEvent.keyboard("{ArrowRight}");
    expect(text.textContent).toBe("epsilon");
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
  });

  it("extends a selection across pages with Shift+click", async () => {
    const { text } = setup(narrative, "{}", pagesOf(12));
    select(text, 6, 10); // "beta" on page 1
    mouseUp(text);
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText(/Selection kept/)).toBeInTheDocument();
    expect(text.querySelector(".mark[data-pending]")).toBeNull();
    select(text, 5, 5); // a click after "gamma"
    mouseUp(text, { shiftKey: true });
    const picker = screen.getByRole("dialog", { name: "Choose a label" });
    expect(within(picker).getByText("beta gamma")).toBeInTheDocument();
    expect(within(picker).getByText("This selection continues across pages.")).toBeInTheDocument();
    chooseLabel("Temporal");
    expect(text.querySelector(".mark")?.textContent).toBe("gamma");
    expect(screen.getByText("An annotation continues from the previous page.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /beta gamma/ })).toBeInTheDocument();
  });

  it("goes to an annotation's page from the panel", async () => {
    const { text } = setup(
      narrative,
      '{"AE":[{"text":"epsilon","start":23,"end":30}]}',
      pagesOf(12),
    );
    await userEvent.click(screen.getByRole("button", { name: /epsilon/ }));
    expect(text.textContent).toBe("epsilon");
    expect(screen.getByText("Page 3 of 3")).toBeInTheDocument();
  });
});

describe("inspecting overlapping annotations (F10)", () => {
  const map =
    '{"SDrug":[{"text":"Aspirin","start":0,"end":7}],' +
    '"CDrug":[{"text":"Aspirin","start":0,"end":7}],' +
    '"Treatment":[{"text":"Aspirin caused","start":0,"end":14}]}';

  it("renders nested spans as flat runs", () => {
    const { text } = setup("Aspirin caused nausea.", map);
    const marks = [...text.querySelectorAll(".mark")].map((m) => m.textContent);
    expect(marks).toEqual(["Aspirin", " caused"]);
    expect(text.textContent).toBe("Aspirin caused nausea.");
  });

  it("lists every annotation under a click, then relabels and deletes", async () => {
    const { text } = setup("Aspirin caused nausea.", map);
    select(text, 3, 3);
    mouseUp(text);
    const inspector = screen.getByRole("dialog", { name: "3 annotations here" });
    const labels = within(inspector).getAllByRole("combobox");
    expect(labels.map((s) => (s as HTMLSelectElement).value)).toEqual([
      "SDrug",
      "CDrug",
      "Treatment",
    ]);

    await userEvent.selectOptions(labels[0] as HTMLSelectElement, "CDrug");
    expect(within(inspector).getByRole("alert")).toHaveTextContent(
      "This text already has the CDrug label.",
    );
    await userEvent.selectOptions(labels[0] as HTMLSelectElement, "ODrug");
    expect(screen.getByRole("heading", { name: /ODrug/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /SDrug/ })).toBeNull();

    const deletes = within(inspector).getAllByRole("button", { name: "Delete" });
    await userEvent.click(deletes[2] as HTMLElement);
    expect(screen.getByRole("dialog", { name: "2 annotations here" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Treatment/ })).toBeNull();
  });

  it("closes everything when clicking unannotated text", () => {
    const { text } = setup("Aspirin caused nausea.", map);
    select(text, 18, 18);
    mouseUp(text);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("saving (F11)", () => {
  it("sends loaded annotations back byte for byte", async () => {
    const loaded = '{ "text":"Aspirin","start":0,"end":7, "weight":1.0E2, "big":12345678901234567890 }';
    const { text } = setup("Aspirin caused nausea.", `{"SDrug":[${loaded}]}`);
    select(text, 15, 21);
    mouseUp(text);
    chooseLabel("AE");
    const fetchMock = stubFetch(json(200, { version: 2, updated_at: "2026-10-27T09:00:00Z" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(
      `{"annotations":{"SDrug":[${loaded}],"AE":[{"text":"nausea","start":15,"end":21}]}}`,
    );
  });

  it("shows report issues next to their annotations", async () => {
    const { text } = setup("Aspirin caused nausea.");
    select(text, 0, 7);
    mouseUp(text);
    chooseLabel("SDrug");
    stubFetch(
      json(422, {
        valid: false,
        records: [],
        issue_count: 2,
        issues: [
          { location: "body", path: "/annotation_sets/user/annotations/SDrug/0/text", code: "TEXT_MISMATCH", message: "Text must match." },
          { location: "body", path: "", code: "X", message: "Something else." },
        ],
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText(/the service found 2 problems/)).toBeInTheDocument();
    expect(screen.getByText("Text must match.")).toHaveClass("item-issue");
    expect(screen.getByText("Something else.")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Unsaved changes");
  });

  it("offers a reload after a version conflict", async () => {
    const { text, onReload } = setup("Aspirin caused nausea.");
    select(text, 0, 7);
    mouseUp(text);
    chooseLabel("SDrug");
    stubFetch(json(412, { error: { code: "VERSION_CONFLICT" } }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText(/saved somewhere else/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reload case" }));
    expect(onReload).toHaveBeenCalledOnce();
  });

  it("explains an unreachable store", async () => {
    const { text } = setup("Aspirin caused nausea.");
    select(text, 0, 7);
    mouseUp(text);
    chooseLabel("SDrug");
    stubFetch(json(503, { error: { code: "STORAGE_UNAVAILABLE" } }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText(/case store isn't reachable/)).toBeInTheDocument();
  });

  it("saves with Ctrl+S", async () => {
    const { text } = setup("Aspirin caused nausea.");
    select(text, 0, 7);
    mouseUp(text);
    chooseLabel("SDrug");
    const fetchMock = stubFetch(json(200, { version: 2, updated_at: "2026-10-27T09:00:00Z" }));
    await act(async () => {
      fireEvent.keyDown(window, { key: "s", ctrlKey: true });
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

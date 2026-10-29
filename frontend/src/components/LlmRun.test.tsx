import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../lib/api";
import { LlmRun } from "./LlmRun";

const ON = { enabled: true, provider: "anthropic", model: "claude-x" } as const;

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("LlmRun", () => {
  it("is hidden when disabled", () => {
    const { container } = render(
      <LlmRun status={{ enabled: false, provider: null, model: null }} onRun={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows elapsed time and progress while running", async () => {
    render(<LlmRun status={ON} onRun={() => new Promise<void>(() => undefined)} />);
    fireEvent.click(screen.getByRole("button", { name: "Annotate with LLM · claude-x" }));
    expect(screen.getByText("Annotating with claude-x…")).toHaveAttribute("aria-live", "polite");
    const bar = screen.getByRole("progressbar");
    expect(bar).not.toHaveAttribute("aria-valuenow");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(65_000);
    });
    expect(screen.getByText("1:05")).toBeInTheDocument();
  });

  it("cancel aborts the request and returns to idle", async () => {
    let signal: AbortSignal | undefined;
    const onRun = vi.fn((s: AbortSignal) => {
      signal = s;
      return new Promise<void>((_, reject) => {
        s.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        });
      });
    });
    render(<LlmRun status={ON} onRun={onRun} />);
    fireEvent.click(screen.getByRole("button", { name: /Annotate with LLM/ }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(signal?.aborted).toBe(true);
    expect(screen.getByRole("button", { name: /Annotate with LLM/ })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the altered-narrative message and retries", async () => {
    const onRun = vi
      .fn<(s: AbortSignal) => Promise<void>>()
      .mockRejectedValueOnce(new ApiError(502, "LLM_OUTPUT_INVALID", "NARRATIVE_ALTERED"))
      .mockResolvedValueOnce(undefined);
    render(<LlmRun status={ON} onRun={onRun} />);
    fireEvent.click(screen.getByRole("button", { name: /Annotate with LLM/ }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The model changed the narrative text, so its annotations can't be used. Running it again may help.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(onRun).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

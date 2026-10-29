import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { LlmSet } from "../lib/record";
import { SourceTabs } from "./SourceTabs";

const sets: LlmSet[] = [
  { id: "a1", provider: "anthropic", model: "claude-x", generatedAt: "2026-10-28T10:00:00Z", annotations: [] },
  { id: "b2", provider: "openai", model: "gpt-y", generatedAt: "2026-10-28T11:00:00Z", annotations: [] },
];

describe("SourceTabs", () => {
  it("lists user and llm sets with badges", () => {
    render(<SourceTabs sets={sets} proposal={null} active="user" userDirty={false} onSelect={vi.fn()} />);
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["My annotations", "LLMclaude-x", "LLMgpt-y"]);
    expect(tabs[1]).toHaveAttribute("title", expect.stringMatching(/^anthropic · claude-x · generated /));
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: /LLM proposal/ })).toBeNull();
  });

  it("shows a proposal tab when there is a proposal", () => {
    render(<SourceTabs sets={[]} proposal={sets[0] ?? null} active="proposal" userDirty={false} onSelect={vi.fn()} />);
    expect(screen.getByRole("tab", { name: "LLM proposal" })).toHaveAttribute("aria-selected", "true");
  });

  it("arrow keys move focus", async () => {
    const onSelect = vi.fn();
    render(<SourceTabs sets={sets} proposal={null} active="user" userDirty={false} onSelect={onSelect} />);
    const tabs = screen.getAllByRole("tab");
    tabs[0]?.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(tabs[1]).toHaveFocus();
    expect(onSelect).toHaveBeenLastCalledWith("a1");
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(tabs[2]).toHaveFocus();
  });

  it("shows the unsaved dot", () => {
    const { rerender } = render(
      <SourceTabs sets={sets} proposal={null} active="user" userDirty={false} onSelect={vi.fn()} />,
    );
    expect(screen.queryByLabelText("unsaved changes")).toBeNull();
    rerender(<SourceTabs sets={sets} proposal={null} active="user" userDirty={true} onSelect={vi.fn()} />);
    expect(screen.getByLabelText("unsaved changes")).toBeInTheDocument();
  });
});

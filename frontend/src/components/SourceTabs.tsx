import { useRef } from "react";
import type { KeyboardEvent } from "react";

import type { LlmSet } from "../lib/record";

/** "user", the id of a stored LLM set, or "proposal". */
export type SourceId = "user" | "proposal" | (string & {});

interface SourceTabsProps {
  readonly sets: readonly LlmSet[];
  readonly proposal: LlmSet | null;
  readonly active: SourceId;
  readonly userDirty: boolean;
  readonly onSelect: (id: SourceId) => void;
}

const WHEN = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

function generated(set: LlmSet): string {
  const at = new Date(set.generatedAt);
  return Number.isNaN(at.getTime()) ? set.generatedAt : WHEN.format(at);
}

export function setTitle(set: LlmSet): string {
  return `${set.provider} · ${set.model} · generated ${generated(set)}`;
}

/** Which annotations the narrative shows: yours, a stored LLM set, or the pending proposal. */
export function SourceTabs({ sets, proposal, active, userDirty, onSelect }: SourceTabsProps) {
  const list = useRef<HTMLDivElement>(null);
  const ids: SourceId[] = ["user", ...sets.map((s) => s.id), ...(proposal === null ? [] : ["proposal"])];

  const onKeyDown = (event: KeyboardEvent) => {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (step === 0 && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const at = ids.indexOf(active);
    let next = (at + step + ids.length) % ids.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = ids.length - 1;
    const id = ids[next];
    if (id === undefined) return;
    onSelect(id);
    list.current?.querySelectorAll<HTMLElement>('[role="tab"]')[next]?.focus();
  };

  const tab = (id: SourceId, title: string | undefined, children: React.ReactNode) => (
    <button
      key={id}
      type="button"
      role="tab"
      className="source-tab"
      aria-selected={id === active}
      tabIndex={id === active ? 0 : -1}
      title={title}
      onClick={() => {
        onSelect(id);
      }}
    >
      {children}
    </button>
  );

  return (
    <div className="source-tabs" role="tablist" aria-label="Annotation sets" ref={list} onKeyDown={onKeyDown}>
      {tab(
        "user",
        undefined,
        <>
          My annotations
          {userDirty && <span className="dirty-dot" role="img" aria-label="unsaved changes" />}
        </>,
      )}
      {sets.map((set) =>
        tab(
          set.id,
          setTitle(set),
          <>
            <span className="llm-badge">LLM</span>
            {set.model}
          </>,
        ),
      )}
      {proposal !== null && tab("proposal", setTitle(proposal), "LLM proposal")}
    </div>
  );
}

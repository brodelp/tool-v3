import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";

import { MAX_ANNOTATION_SETS, type Label } from "../generated/labels";
import { usePages, type MeasurerFactory } from "../hooks/usePages";
import {
  assignLanes,
  isDuplicate,
  newAnnotation,
  serializeMap,
  type Annotation,
  type SerializedMap,
} from "../lib/annotations";
import {
  acceptProposal,
  ApiError,
  describeError,
  discardProposal,
  proposeAnnotations,
  removeLlmSet,
  saveUserSet,
  type Issue,
  type LlmStatus,
  type Proposal,
} from "../lib/api";
import { pageOf } from "../lib/paginate";
import { byCodePoint, type LlmSet, type LoadedSubmission } from "../lib/record";
import { coveringAt, segment, type Range } from "../lib/segments";
import { clearSelection, readSelection } from "../lib/selection";
import { AnnotationPanel } from "./AnnotationPanel";
import { segmentStyle } from "./highlight";
import { Inspector } from "./Inspector";
import { LabelPicker } from "./LabelPicker";
import { LlmRun } from "./LlmRun";
import { ProposalBar } from "./ProposalBar";
import { SourceTabs, setTitle, type SourceId } from "./SourceTabs";

interface AnnotatorProps {
  readonly submission: LoadedSubmission;
  readonly onBack: () => void;
  /** Load the latest version from the server again (after a version conflict). */
  readonly onReload: () => void;
  readonly measurer?: MeasurerFactory;
  /** Whether LLM annotation is configured; absent means it is not offered. */
  readonly llm?: LlmStatus;
}

const NO_LLM: LlmStatus = { enabled: false, provider: null, model: null };

type Popup =
  | { readonly kind: "pick"; readonly x: number; readonly y: number; readonly message: string | null }
  | {
      readonly kind: "inspect";
      readonly x: number;
      readonly y: number;
      readonly ids: readonly string[];
      readonly message: string | null;
    };

type SaveState =
  | { readonly kind: "idle" }
  | { readonly kind: "saving" }
  | { readonly kind: "saved"; readonly at: string }
  | { readonly kind: "invalid"; readonly count: number }
  | { readonly kind: "conflict" }
  | { readonly kind: "error"; readonly message: string };

interface PlacedIssues {
  readonly byId: ReadonlyMap<string, readonly Issue[]>;
  readonly other: readonly Issue[];
}

const NO_ISSUES: PlacedIssues = { byId: new Map(), other: [] };

/** Report paths are `/annotation_sets/user/annotations/<label>/<index>[/field]` (stage 4 §4.2). */
function placeIssues(issues: readonly Issue[], map: SerializedMap): PlacedIssues {
  const byId = new Map<string, Issue[]>();
  const other: Issue[] = [];
  for (const issue of issues) {
    const [, root, set, key, label, index] = issue.path.split("/");
    const id = root === "annotation_sets" && set === "user" && key === "annotations" ? map.positions.get(`${label ?? ""}/${index ?? ""}`) : undefined;
    if (id === undefined) other.push(issue);
    else byId.set(id, [...(byId.get(id) ?? []), issue]);
  }
  return { byId, other };
}

function isEditable(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLSelectElement ||
    target instanceof HTMLTextAreaElement
  );
}

const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

export function Annotator({ submission, onBack, onReload, measurer, llm = NO_LLM }: AnnotatorProps) {
  const { narrative, caseId, username } = submission;
  const text = narrative.text;
  const [userAnnotations, setAnnotations] = useState<readonly Annotation[]>(submission.userAnnotations);
  const [llmSets, setLlmSets] = useState<readonly LlmSet[]>(submission.llmSets);
  const [active, setActive] = useState<SourceId>("user");
  const [removeError, setRemoveError] = useState<string | null>(null);
  const readOnly = active !== "user";
  // Tabs only change what is shown; `userAnnotations` keeps unsaved edits meanwhile.
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [accepting, setAccepting] = useState(false);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const proposalRef = useRef<Proposal | null>(null);
  useEffect(() => {
    proposalRef.current = proposal;
  }, [proposal]);
  const storedSet = readOnly ? llmSets.find((set) => set.id === active) : undefined;
  const activeSet = active === "proposal" ? proposal?.set : storedSet;
  const annotations = useMemo(
    () => (readOnly ? (activeSet?.annotations ?? []) : userAnnotations),
    [readOnly, activeSet, userAnnotations],
  );
  const [version, setVersion] = useState(submission.version);
  const [savedText, setSavedText] = useState(() => serializeMap(submission.userAnnotations).text);
  const [pending, setPending] = useState<Range | null>(null);
  const [popup, setPopup] = useState<Popup | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [anchor, setAnchor] = useState(0); // UTF-16 position kept on screen across re-pagination
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  const [issues, setIssues] = useState<PlacedIssues>(NO_ISSUES);
  const [area, setArea] = useState<HTMLDivElement | null>(null);
  const textRef = useRef<HTMLDivElement>(null);

  const starts = usePages(text, area, measurer);
  const page = pageOf(starts, anchor);
  const pageStart = narrative.toCodePoint(starts[page] ?? 0);
  const pageEnd = narrative.toCodePoint(starts[page + 1] ?? text.length);
  const pageCount = starts.length;

  const serialized = useMemo(() => serializeMap(userAnnotations), [userAnnotations]);
  const dirty = serialized.text !== savedText;
  const lanes = useMemo(() => assignLanes(annotations), [annotations]);
  const segments = useMemo(
    () => segment(pageStart, pageEnd, annotations, pending),
    [pageStart, pageEnd, annotations, pending],
  );
  const continuesBefore = annotations.some((a) => a.start < pageStart && a.end > pageStart);
  const continuesAfter = annotations.some((a) => a.start < pageEnd && a.end > pageEnd);

  const goTo = useCallback(
    (target: number) => {
      const k = Math.max(0, Math.min(target, starts.length - 1));
      setAnchor(starts[k] ?? 0);
      setPopup(null); // a pending selection survives paging, so it can be extended
    },
    [starts],
  );

  const cancel = useCallback(() => {
    setPopup(null);
    setPending(null);
    clearSelection();
  }, []);

  const save = useCallback(async () => {
    if (saveState.kind === "saving") return;
    const sent = serialized;
    setSaveState({ kind: "saving" });
    try {
      const result = await saveUserSet(caseId, username, version, sent.text);
      if (result.kind === "saved") {
        setVersion(result.version);
        setSavedText(sent.text);
        setIssues(NO_ISSUES);
        setSaveState({ kind: "saved", at: result.updatedAt });
      } else if (result.kind === "invalid") {
        setIssues(placeIssues(result.issues, sent));
        setSaveState({ kind: "invalid", count: result.issueCount });
      } else {
        setSaveState({ kind: "conflict" });
      }
    } catch (error) {
      setSaveState({ kind: "error", message: describeError(error) });
    }
  }, [caseId, username, version, serialized, saveState.kind]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (dirty) void save();
        return;
      }
      if (isEditable(event.target) || event.defaultPrevented) return;
      if (event.key === "Escape") cancel();
      else if (popup !== null) return;
      else if (event.key === "ArrowRight" || event.key === "PageDown") goTo(page + 1);
      else if (event.key === "ArrowLeft" || event.key === "PageUp") goTo(page - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [dirty, save, cancel, goTo, page, popup]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
    };
  }, [dirty]);

  const onMouseUp = (event: ReactMouseEvent) => {
    const root = textRef.current;
    if (root === null) return;
    const selection = readSelection(root);
    if (selection === null) return;
    const base = starts[page] ?? 0;
    const a = narrative.toCodePoint(base + selection.start);
    const b = narrative.toCodePoint(base + selection.end);
    const at = { x: event.clientX, y: event.clientY };
    if (selection.collapsed) {
      if (!readOnly && event.shiftKey && pending !== null) {
        const extended = { start: Math.min(pending.start, a), end: Math.max(pending.end, a) };
        setPending(extended);
        setPopup({ kind: "pick", ...at, message: null });
        return;
      }
      // A click lands between characters: inspect what covers the character after or before it.
      const hits = coveringAt(annotations, a);
      const found = hits.length > 0 ? hits : coveringAt(annotations, a - 1);
      if (found.length > 0) {
        setPending(null);
        setSelectedId(found[0]?.id ?? null);
        setPopup({ kind: "inspect", ...at, ids: found.map((h) => h.id), message: null });
      } else {
        setPopup(null);
        setPending(null);
        setSelectedId(null);
      }
      return;
    }
    if (readOnly) return; // LLM sets cannot be annotated
    let range: Range = { start: Math.min(a, b), end: Math.max(a, b) };
    if (event.shiftKey && pending !== null) {
      range = { start: Math.min(range.start, pending.start), end: Math.max(range.end, pending.end) };
    }
    setPending(range);
    setSelectedId(null);
    setPopup({ kind: "pick", ...at, message: null });
  };

  const pick = (label: Label) => {
    if (pending === null) return;
    if (isDuplicate(userAnnotations, label, pending.start, pending.end)) {
      setPopup((p) =>
        p?.kind === "pick" ? { ...p, message: `This text already has the ${label} label.` } : p,
      );
      return;
    }
    const created = newAnnotation(narrative, label, pending.start, pending.end);
    setAnnotations([...userAnnotations, created]);
    setSelectedId(created.id);
    setPending(null);
    setPopup(null);
    clearSelection();
  };

  const relabel = (id: string, label: Label) => {
    const target = userAnnotations.find((a) => a.id === id);
    if (target === undefined) return;
    if (isDuplicate(userAnnotations, label, target.start, target.end, id)) {
      setPopup((p) =>
        p?.kind === "inspect" ? { ...p, message: `This text already has the ${label} label.` } : p,
      );
      return;
    }
    setAnnotations(userAnnotations.map((a) => (a.id === id ? { ...a, label } : a)));
    setPopup((p) => (p?.kind === "inspect" ? { ...p, message: null } : p));
  };

  const remove = (id: string) => {
    setAnnotations(userAnnotations.filter((a) => a.id !== id));
    if (selectedId === id) setSelectedId(null);
    setPopup((p) => {
      if (p?.kind !== "inspect") return p;
      const ids = p.ids.filter((x) => x !== id);
      return ids.length > 0 ? { ...p, ids, message: null } : null;
    });
  };

  const choose = (annotation: Annotation) => {
    setAnchor(starts[pageOf(starts, narrative.toUnit(annotation.start))] ?? 0);
    setSelectedId(annotation.id);
    setPending(null);
    setPopup(null);
  };

  const select = (id: SourceId) => {
    setActive(id);
    setPending(null);
    setPopup(null);
    setSelectedId(null);
    clearSelection();
  };

  const runModel = async (signal: AbortSignal) => {
    const made = await proposeAnnotations(caseId, username, signal);
    const old = proposalRef.current;
    if (old !== null) discardProposal(caseId, username, old.proposalId).catch(() => undefined);
    setProposalError(null);
    setProposal(made);
    select("proposal");
  };

  const accept = async () => {
    if (proposal === null || accepting) return;
    setAccepting(true);
    setProposalError(null);
    try {
      const result = await acceptProposal(caseId, username, proposal.proposalId, version);
      if (result.kind === "saved") {
        const set = proposal.set;
        setVersion(result.version);
        setLlmSets((sets) => [...sets.filter((s) => s.id !== set.id), set].sort((a, b) => byCodePoint(a.id, b.id)));
        setProposal(null);
        select(set.id);
      } else if (result.kind === "invalid") {
        const n = result.issueCount;
        const tooManySets = result.issues.some(
          (issue) => issue.code === "LIMIT_EXCEEDED" && issue.path === "/annotation_sets",
        );
        setProposalError(
          tooManySets
            ? `This case already has ${MAX_ANNOTATION_SETS} annotation sets. Remove one before accepting another.`
            : `Not accepted: the service found ${n} ${n === 1 ? "problem" : "problems"} in the proposal.`,
        );
      } else {
        setSaveState({ kind: "conflict" });
      }
    } catch (error) {
      if (error instanceof ApiError && error.code === "PROPOSAL_NOT_FOUND") {
        setProposal(null);
        select("user");
      }
      setProposalError(`Not accepted. ${describeError(error)}`);
    } finally {
      setAccepting(false);
    }
  };

  const discard = () => {
    if (proposal === null) return;
    discardProposal(caseId, username, proposal.proposalId).catch(() => undefined);
    setProposal(null);
    setProposalError(null);
    select("user");
  };

  const removeSet = async (set: LlmSet) => {
    if (!window.confirm(`Remove the annotations from ${set.model}? This creates a new version.`)) return;
    setRemoveError(null);
    try {
      const result = await removeLlmSet(caseId, username, set.id, version);
      if (result.kind === "saved") {
        setVersion(result.version);
        setLlmSets((sets) => sets.filter((s) => s.id !== set.id));
        select("user");
      } else {
        setSaveState({ kind: "conflict" });
      }
    } catch (error) {
      setRemoveError(describeError(error));
    }
  };

  const back = () => {
    if (!dirty || window.confirm("Leave this case? Your unsaved changes will be lost.")) onBack();
  };

  let status: string;
  if (saveState.kind === "saving") status = "Saving…";
  else if (dirty) status = "Unsaved changes";
  else if (saveState.kind === "saved") status = `Saved at ${TIME.format(new Date(saveState.at))}`;
  else status = "All changes saved";

  const inspected =
    popup?.kind === "inspect"
      ? popup.ids
          .map((id) => annotations.find((a) => a.id === id))
          .filter((a): a is Annotation => a !== undefined)
      : [];

  return (
    <div className="workspace">
      <header className="case-bar">
        <button type="button" className="quiet" onClick={back}>
          All cases
        </button>
        <h1 className="case-id">{caseId}</h1>
        <span className="case-version">Version {version}</span>
        <LlmRun status={llm} onRun={runModel} disabled={accepting} />
        <span className="save-status" role="status">
          {status}
        </span>
        <button
          type="button"
          className="primary"
          disabled={!dirty || saveState.kind === "saving"}
          onClick={() => void save()}
        >
          Save
        </button>
      </header>

      {saveState.kind === "conflict" && (
        <div className="banner" role="alert">
          <p>
            This case was saved somewhere else after you opened it. Reload it to continue; your
            unsaved changes here will be discarded.
          </p>
          <button type="button" className="primary" onClick={onReload}>
            Reload case
          </button>
        </div>
      )}
      {saveState.kind === "error" && (
        <div className="banner" role="alert">
          <p>Not saved. {saveState.message}</p>
        </div>
      )}
      {removeError !== null && (
        <div className="banner" role="alert">
          <p>Not removed. {removeError}</p>
        </div>
      )}
      {proposalError !== null && (
        <div className="banner" role="alert">
          <p>{proposalError}</p>
        </div>
      )}
      {saveState.kind === "invalid" && (
        <div className="banner" role="alert">
          <p>
            Not saved: the service found {saveState.count}{" "}
            {saveState.count === 1 ? "problem" : "problems"}. Each is listed with its annotation.
          </p>
        </div>
      )}

      <SourceTabs sets={llmSets} proposal={proposal?.set ?? null} active={active} userDirty={dirty} onSelect={select} />
      {active === "proposal" && proposal !== null && (
        <ProposalBar proposal={proposal} busy={accepting} onAccept={() => void accept()} onDiscard={discard} />
      )}
      {storedSet !== undefined && (
        <div className="set-bar">
          <span className="set-info">{setTitle(storedSet)}</span>
          <span className="set-note">LLM annotations are read-only.</span>
          <button type="button" className="quiet danger" onClick={() => void removeSet(storedSet)}>
            Remove set
          </button>
        </div>
      )}

      <main className="desk">
        <section className="paper" aria-label={`Narrative, page ${page + 1} of ${pageCount}`}>
          <div className="page-body" ref={setArea}>
            <div className="page-text" ref={textRef} onMouseUp={onMouseUp}>
              {segments.map((seg) => {
                const content = narrative.slice(seg.start, seg.end);
                if (seg.covering.length === 0 && !seg.pending) {
                  return <Fragment key={seg.start}>{content}</Fragment>;
                }
                return (
                  <span
                    key={seg.start}
                    className="mark"
                    data-pending={seg.pending || undefined}
                    style={segmentStyle(seg, lanes, selectedId)}
                  >
                    {content}
                  </span>
                );
              })}
            </div>
          </div>
          <footer className="folio">
            <button
              type="button"
              className="quiet"
              disabled={page === 0}
              onClick={() => {
                goTo(page - 1);
              }}
            >
              Previous page
            </button>
            <span className="folio-page">
              Page {page + 1} of {pageCount}
            </span>
            <button
              type="button"
              className="quiet"
              disabled={page >= pageCount - 1}
              onClick={() => {
                goTo(page + 1);
              }}
            >
              Next page
            </button>
          </footer>
          {(continuesBefore || continuesAfter || (pending !== null && popup === null)) && (
            <div className="page-notes">
              {continuesBefore && <p>An annotation continues from the previous page.</p>}
              {continuesAfter && <p>An annotation continues on the next page.</p>}
              {pending !== null && popup === null && (
                <p>
                  Selection kept. Shift+click where it should end, or{" "}
                  <button
                    type="button"
                    className="link"
                    onClick={(event) => {
                      setPopup({ kind: "pick", x: event.clientX, y: event.clientY, message: null });
                    }}
                  >
                    choose a label
                  </button>
                  .
                </p>
              )}
            </div>
          )}
        </section>

        <AnnotationPanel
          annotations={annotations}
          readOnly={readOnly}
          selectedId={selectedId}
          issuesById={issues.byId}
          otherIssues={issues.other}
          onChoose={choose}
        />
      </main>

      {popup?.kind === "pick" && pending !== null && (
        <LabelPicker
          x={popup.x}
          y={popup.y}
          quote={narrative.slice(pending.start, pending.end)}
          crossesPages={pending.start < pageStart || pending.end > pageEnd}
          message={popup.message}
          onPick={pick}
          onClose={cancel}
        />
      )}
      {popup?.kind === "inspect" && inspected.length > 0 && (
        <Inspector
          x={popup.x}
          y={popup.y}
          annotations={inspected}
          message={popup.message}
          readOnly={readOnly}
          onRelabel={relabel}
          onDelete={remove}
          onClose={() => {
            setPopup(null);
          }}
        />
      )}
    </div>
  );
}

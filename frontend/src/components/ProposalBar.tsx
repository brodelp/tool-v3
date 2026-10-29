import { LABELS } from "../generated/labels";
import type { Proposal } from "../lib/api";

interface ProposalBarProps {
  readonly proposal: Proposal;
  readonly busy: boolean;
  readonly onAccept: () => void;
  readonly onDiscard: () => void;
}

/** The decision for a proposal: nothing is stored until it is accepted (stage 4 §6). */
export function ProposalBar({ proposal, busy, onAccept, onDiscard }: ProposalBarProps) {
  const counts = LABELS.filter((label) => (proposal.countsByLabel[label] ?? 0) > 0);
  return (
    <div className="proposal-bar">
      <strong className="proposal-title">LLM proposal — not saved</strong>
      <span className="proposal-counts">
        {proposal.count} {proposal.count === 1 ? "annotation" : "annotations"}
        {counts.length > 0 && ": "}
        {counts.map((label, i) => (
          <span key={label}>
            {i > 0 && ", "}
            {label} {proposal.countsByLabel[label]}
          </span>
        ))}
      </span>
      {proposal.replacesExistingSet && (
        <span className="proposal-note">
          Accepting replaces the stored set from {proposal.set.model}.
        </span>
      )}
      <span className="proposal-actions">
        <button type="button" className="quiet" disabled={busy} onClick={onDiscard}>
          Discard
        </button>
        <button type="button" className="primary" disabled={busy} onClick={onAccept}>
          Accept
        </button>
      </span>
    </div>
  );
}

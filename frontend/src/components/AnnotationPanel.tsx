import { LABELS } from "../generated/labels";
import { byPosition, type Annotation } from "../lib/annotations";
import type { Issue } from "../lib/api";
import { inkStyle } from "../lib/colors";

interface AnnotationPanelProps {
  readonly annotations: readonly Annotation[];
  readonly readOnly: boolean;
  readonly selectedId: string | null;
  /** Issues from the last rejected save, by annotation id; others are listed apart. */
  readonly issuesById: ReadonlyMap<string, readonly Issue[]>;
  readonly otherIssues: readonly Issue[];
  readonly onChoose: (annotation: Annotation) => void;
}

const QUOTE_LIMIT = 60;

/** Annotations grouped by label in canonical order, each group in narrative order. */
export function AnnotationPanel(props: AnnotationPanelProps) {
  const { annotations, readOnly, selectedId, issuesById, otherIssues, onChoose } = props;
  const groups = LABELS.map((label) => ({
    label,
    items: annotations.filter((a) => a.label === label).sort(byPosition),
  })).filter((group) => group.items.length > 0);

  return (
    <aside className="panel" aria-label="Annotations">
      <h2 className="panel-title">
        Annotations <span className="count">{annotations.length}</span>
      </h2>
      {otherIssues.length > 0 && (
        <ul className="issue-list" aria-label="Save problems">
          {otherIssues.map((issue, i) => (
            <li key={i}>{issue.message}</li>
          ))}
        </ul>
      )}
      {groups.length === 0 ? (
        <p className="panel-empty">
          {readOnly
            ? "This set has no annotations."
            : "Select text in the narrative, then choose a label to annotate it."}
        </p>
      ) : (
        groups.map((group) => (
          <section key={group.label} className="label-group" style={inkStyle(group.label)}>
            <h3 className="label-key">
              {group.label} <span className="count">{group.items.length}</span>
            </h3>
            <ul>
              {group.items.map((a) => {
                const issues = issuesById.get(a.id) ?? [];
                const quote =
                  a.text.length > QUOTE_LIMIT ? `${a.text.slice(0, QUOTE_LIMIT)}…` : a.text;
                return (
                  <li key={a.id}>
                    <button
                      type="button"
                      className="panel-item"
                      aria-current={a.id === selectedId ? "true" : undefined}
                      onClick={() => {
                        onChoose(a);
                      }}
                    >
                      <span className="panel-quote">{quote}</span>
                      <span className="offsets">
                        {a.start}–{a.end}
                      </span>
                    </button>
                    {issues.map((issue, i) => (
                      <p key={i} className="item-issue">
                        {issue.message}
                      </p>
                    ))}
                  </li>
                );
              })}
            </ul>
          </section>
        ))
      )}
    </aside>
  );
}

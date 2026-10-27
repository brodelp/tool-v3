import { LABELS, type Label } from "../generated/labels";
import { isLabel, type Annotation } from "../lib/annotations";
import { inkStyle } from "../lib/colors";
import { Popover } from "./Popover";

interface InspectorProps {
  readonly x: number;
  readonly y: number;
  readonly annotations: readonly Annotation[];
  readonly message: string | null;
  readonly onRelabel: (id: string, label: Label) => void;
  readonly onDelete: (id: string) => void;
  readonly onClose: () => void;
}

const QUOTE_LIMIT = 80;

/** Every annotation under the clicked point, with relabel and delete. */
export function Inspector(props: InspectorProps) {
  const { annotations, message, onRelabel, onDelete, onClose } = props;
  const title = annotations.length === 1 ? "Annotation" : `${annotations.length} annotations here`;
  return (
    <Popover x={props.x} y={props.y} label={title} onClose={onClose}>
      <p className="inspector-title">{title}</p>
      {message !== null && (
        <p className="picker-message" role="alert">
          {message}
        </p>
      )}
      <ul className="inspector-list">
        {annotations.map((a, i) => {
          const quote = a.text.length > QUOTE_LIMIT ? `${a.text.slice(0, QUOTE_LIMIT)}…` : a.text;
          return (
            <li
              key={a.id}
              className="inspector-item"
              style={inkStyle(a.label)}
            >
              <div className="inspector-quote">{quote}</div>
              <div className="inspector-meta">
                <span className="offsets">
                  {a.start}–{a.end}
                </span>
              </div>
              <div className="inspector-actions">
                <select
                  aria-label={`Label of annotation ${i + 1}`}
                  value={a.label}
                  autoFocus={i === 0}
                  onChange={(event) => {
                    const value = event.target.value;
                    if (isLabel(value)) onRelabel(a.id, value);
                  }}
                >
                  {LABELS.map((label) => (
                    <option key={label} value={label}>
                      {label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="quiet danger"
                  onClick={() => {
                    onDelete(a.id);
                  }}
                >
                  Delete
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </Popover>
  );
}

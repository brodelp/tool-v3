import { useId, useMemo, useState } from "react";

import { LABELS, type Label } from "../generated/labels";
import { inkStyle } from "../lib/colors";
import { Popover } from "./Popover";

interface LabelPickerProps {
  readonly x: number;
  readonly y: number;
  readonly quote: string;
  readonly crossesPages: boolean;
  readonly message: string | null;
  readonly onPick: (label: Label) => void;
  readonly onClose: () => void;
}

/** Labels whose key starts with the filter first, then those containing it; canonical order. */
export function filterLabels(filter: string): Label[] {
  const needle = filter.trim().toLowerCase();
  if (needle === "") return [...LABELS];
  const starts = LABELS.filter((l) => l.toLowerCase().startsWith(needle));
  const contains = LABELS.filter(
    (l) => !l.toLowerCase().startsWith(needle) && l.toLowerCase().includes(needle),
  );
  return [...starts, ...contains];
}

const QUOTE_LIMIT = 140;

export function LabelPicker(props: LabelPickerProps) {
  const { quote, crossesPages, message, onPick, onClose } = props;
  const [filter, setFilter] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const options = useMemo(() => filterLabels(filter), [filter]);
  const current = Math.min(active, Math.max(options.length - 1, 0));
  const shown = quote.length > QUOTE_LIMIT ? `${quote.slice(0, QUOTE_LIMIT)}…` : quote;

  return (
    <Popover x={props.x} y={props.y} label="Choose a label" onClose={onClose}>
      <blockquote className="picker-quote">{shown}</blockquote>
      {crossesPages && (
        <p className="picker-note">This selection continues across pages.</p>
      )}
      <input
        className="picker-filter"
        autoFocus
        aria-label="Filter labels"
        aria-controls={listId}
        aria-activedescendant={options.length > 0 ? `${listId}-${current}` : undefined}
        placeholder="Type to filter labels"
        value={filter}
        onChange={(event) => {
          setFilter(event.target.value);
          setActive(0);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowRight") {
            event.preventDefault();
            setActive((current + 1) % Math.max(options.length, 1));
          } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
            event.preventDefault();
            setActive((current - 1 + options.length) % Math.max(options.length, 1));
          } else if (event.key === "Enter") {
            event.preventDefault();
            const label = options[current];
            if (label !== undefined) onPick(label);
          }
        }}
      />
      {message !== null && (
        <p className="picker-message" role="alert">
          {message}
        </p>
      )}
      <ul className="picker-list" id={listId} role="listbox" aria-label="Labels">
        {options.map((label, i) => (
          <li
            key={label}
            id={`${listId}-${i}`}
            role="option"
            aria-selected={i === current}
            className="picker-option"
            style={inkStyle(label)}
            onMouseEnter={() => {
              setActive(i);
            }}
            onMouseDown={(event) => {
              event.preventDefault(); // keep focus in the filter
            }}
            onClick={() => {
              onPick(label);
            }}
          >
            {label}
          </li>
        ))}
        {options.length === 0 && <li className="picker-empty">No label matches.</li>}
      </ul>
    </Popover>
  );
}

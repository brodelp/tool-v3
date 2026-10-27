import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

interface PopoverProps {
  readonly x: number;
  readonly y: number;
  readonly label: string;
  readonly onClose: () => void;
  readonly children: ReactNode;
}

const MARGIN = 12;

/** A small dialog next to the pointer, kept inside the viewport. */
export function Popover({ x, y, label, onClose, children }: PopoverProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: x, top: y + MARGIN });

  useLayoutEffect(() => {
    const box = ref.current?.getBoundingClientRect();
    if (box === undefined) return;
    const left = Math.max(MARGIN, Math.min(x - 24, window.innerWidth - box.width - MARGIN));
    const below = y + MARGIN;
    const top =
      below + box.height > window.innerHeight - MARGIN
        ? Math.max(MARGIN, y - box.height - MARGIN)
        : below;
    setPosition({ left, top });
  }, [x, y]);

  return (
    <div
      ref={ref}
      className="popover"
      role="dialog"
      aria-label={label}
      style={{ left: position.left, top: position.top }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onClose();
        }
      }}
      onMouseUp={(event) => {
        event.stopPropagation();
      }}
    >
      {children}
    </div>
  );
}

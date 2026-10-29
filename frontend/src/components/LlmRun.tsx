import { useEffect, useRef, useState } from "react";

import { describeError, type LlmStatus } from "../lib/api";

interface LlmRunProps {
  readonly status: LlmStatus;
  /** Resolves when the run ended (its result is the caller's business); rejects on failure. */
  readonly onRun: (signal: AbortSignal) => Promise<void>;
  /** True while something else (an accept) is in flight; a run cannot start then. */
  readonly disabled?: boolean;
}

type RunState =
  | { readonly kind: "idle" }
  | { readonly kind: "running" }
  | { readonly kind: "failed"; readonly message: string };

function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** Starts a model run and shows that it is working (stage 4 §6, "Annotate with LLM"). */
export function LlmRun({ status, onRun, disabled = false }: LlmRunProps) {
  const [state, setState] = useState<RunState>({ kind: "idle" });
  const [elapsed, setElapsed] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const running = state.kind === "running";

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      setElapsed((seconds) => seconds + 1);
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [running]);

  useEffect(
    () => () => {
      controller.current?.abort();
    },
    [],
  );

  if (!status.enabled) return null;
  const model = status.model ?? "model";

  const run = () => {
    const own = new AbortController();
    controller.current = own;
    setElapsed(0);
    setState({ kind: "running" });
    onRun(own.signal).then(
      () => {
        if (controller.current === own) setState({ kind: "idle" });
      },
      (error: unknown) => {
        if (controller.current !== own || own.signal.aborted) return;
        setState({ kind: "failed", message: describeError(error) });
      },
    );
  };

  const cancel = () => {
    controller.current?.abort();
    setState({ kind: "idle" });
  };

  if (state.kind === "running") {
    return (
      <div className="llm-run" data-running>
        <span className="llm-run-text" aria-live="polite">
          Annotating with {model}…
        </span>
        <span className="llm-elapsed">{clock(elapsed)}</span>
        <div className="llm-progress" role="progressbar" aria-label="Annotating">
          <div className="llm-progress-bar" />
        </div>
        <button type="button" className="quiet" onClick={cancel}>
          Cancel
        </button>
      </div>
    );
  }
  return (
    <div className="llm-run">
      {state.kind === "failed" && (
        <p className="llm-run-error" role="alert">
          {state.message}
        </p>
      )}
      <button
        type="button"
        className={state.kind === "failed" ? "primary" : "quiet"}
        onClick={run}
        disabled={disabled}
      >
        {state.kind === "failed" ? "Try again" : `Annotate with LLM · ${model}`}
      </button>
    </div>
  );
}

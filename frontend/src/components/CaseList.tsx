import { useEffect, useState } from "react";

import { describeError, listSubmissions, type SubmissionSummary } from "../lib/api";

interface CaseListProps {
  readonly username: string;
  readonly onOpen: (caseId: string) => void;
}

type ListState =
  | { readonly kind: "loading" }
  | { readonly kind: "ready"; readonly cases: readonly SubmissionSummary[] }
  | { readonly kind: "failed"; readonly message: string };

const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function CaseList({ username, onOpen }: CaseListProps) {
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    listSubmissions(username).then(
      (cases) => {
        if (current) setState({ kind: "ready", cases });
      },
      (error: unknown) => {
        if (current) setState({ kind: "failed", message: describeError(error) });
      },
    );
    return () => {
      current = false;
    };
  }, [username, attempt]);

  if (state.kind === "loading") return <p className="quiet-note">Loading cases…</p>;
  if (state.kind === "failed") {
    return (
      <div className="banner" role="alert">
        <p>{state.message}</p>
        <button
          type="button"
          className="primary"
          onClick={() => {
            setState({ kind: "loading" });
            setAttempt(attempt + 1);
          }}
        >
          Try again
        </button>
      </div>
    );
  }
  if (state.cases.length === 0) {
    return (
      <p className="quiet-note">
        No cases are stored for this username yet. Cases arrive through the submissions API; once
        one is submitted under this username, it appears here.
      </p>
    );
  }
  return (
    <table className="cases">
      <caption className="visually-hidden">Cases stored for this username</caption>
      <thead>
        <tr>
          <th scope="col">Case</th>
          <th scope="col">Last saved</th>
          <th scope="col">Version</th>
        </tr>
      </thead>
      <tbody>
        {state.cases.map((entry) => (
          <tr key={entry.case_id}>
            <th scope="row">
              <button
                type="button"
                className="case-link"
                onClick={() => {
                  onOpen(entry.case_id);
                }}
              >
                {entry.case_id}
              </button>
            </th>
            <td>
              {entry.version === 1 ? "Not saved yet" : DATE.format(new Date(entry.updated_at))}
            </td>
            <td className="numeric">{entry.version}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

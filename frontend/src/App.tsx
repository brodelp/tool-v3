import { useEffect, useState } from "react";

import { Annotator } from "./components/Annotator";
import { CaseList } from "./components/CaseList";
import type { MeasurerFactory } from "./hooks/usePages";
import { describeError, loadSubmission } from "./lib/api";
import type { LoadedSubmission } from "./lib/record";

const USERNAME_KEY = "scat.username";

/** The username is a remembered convenience, not an identity (stage 3 §4.1, §7). */
function readUsername(): string {
  try {
    return window.localStorage.getItem(USERNAME_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeUsername(value: string): void {
  try {
    window.localStorage.setItem(USERNAME_KEY, value);
  } catch {
    // Storage may be unavailable (private windows); the username just isn't remembered.
  }
}

type Opened =
  | { readonly kind: "loading"; readonly caseId: string }
  | { readonly kind: "ready"; readonly submission: LoadedSubmission; readonly key: number }
  | { readonly kind: "failed"; readonly caseId: string; readonly message: string };

interface AppProps {
  readonly measurer?: MeasurerFactory;
}

export function App({ measurer }: AppProps) {
  const [username, setUsername] = useState(readUsername);
  const [draft, setDraft] = useState(username);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [opened, setOpened] = useState<Opened | null>(null);
  const [reloads, setReloads] = useState(0);

  useEffect(() => {
    if (caseId === null || username === "") return;
    let current = true;
    loadSubmission(caseId, username).then(
      (submission) => {
        if (current) setOpened({ kind: "ready", submission, key: reloads });
      },
      (error: unknown) => {
        if (current) setOpened({ kind: "failed", caseId, message: describeError(error) });
      },
    );
    return () => {
      current = false;
    };
  }, [caseId, username, reloads]);

  const open = (id: string) => {
    setOpened({ kind: "loading", caseId: id });
    setCaseId(id);
  };
  const close = () => {
    setCaseId(null);
    setOpened(null);
  };

  if (caseId !== null && opened !== null) {
    if (opened.kind === "ready") {
      return (
        <Annotator
          key={opened.key}
          submission={opened.submission}
          onBack={close}
          onReload={() => {
            setReloads(reloads + 1);
          }}
          {...(measurer === undefined ? {} : { measurer })}
        />
      );
    }
    return (
      <div className="front">
        {opened.kind === "loading" ? (
          <p className="quiet-note">Opening {opened.caseId}…</p>
        ) : (
          <div className="banner" role="alert">
            <p>{opened.message}</p>
            <button type="button" className="primary" onClick={close}>
              Back to cases
            </button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="front">
      <header className="front-head">
        <h1 className="wordmark">SCAT annotator</h1>
        <p className="front-lede">
          Mark up case narratives with the configured safety-concept labels.
        </p>
      </header>
      <form
        className="who"
        onSubmit={(event) => {
          event.preventDefault();
          const value = draft;
          writeUsername(value);
          setUsername(value);
        }}
      >
        <label htmlFor="username">Username</label>
        <div className="who-row">
          <input
            id="username"
            name="username"
            autoComplete="username"
            spellCheck={false}
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value);
            }}
          />
          <button type="submit" className="primary" disabled={draft === ""}>
            Show cases
          </button>
        </div>
        <p className="hint">
          There is no sign-in: a username only selects whose cases to show.
        </p>
      </form>
      {username !== "" && (
        <section className="case-section" aria-label={`Cases for ${username}`}>
          <CaseList key={username} username={username} onOpen={open} />
        </section>
      )}
    </div>
  );
}

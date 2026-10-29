import { parseExport, type LoadedSubmission } from "./record";

/** Same-origin API (stage 3 §6). Nothing here logs request or response content. */
const BASE = "/api/v1/submissions";

export interface SubmissionSummary {
  readonly case_id: string;
  readonly submitted_at: string;
  readonly updated_at: string;
  readonly version: number;
}

export interface Issue {
  readonly location: string;
  readonly path: string;
  readonly code: string;
  readonly message: string;
}

export type SaveResult =
  | { readonly kind: "saved"; readonly version: number; readonly updatedAt: string }
  | { readonly kind: "invalid"; readonly issues: readonly Issue[]; readonly issueCount: number }
  | { readonly kind: "conflict" };

/** A failure the UI explains by its stable error code (or `null` when there is no body). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | null,
  ) {
    super(`API request failed with status ${status}`);
    this.name = "ApiError";
  }
}

function query(username: string): string {
  return `username=${encodeURIComponent(username)}`;
}

async function failure(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code?: string } };
    return new ApiError(response.status, body.error?.code ?? null);
  } catch {
    return new ApiError(response.status, null);
  }
}

export async function listSubmissions(username: string): Promise<SubmissionSummary[]> {
  const response = await fetch(`${BASE}?${query(username)}`, { cache: "no-store" });
  if (!response.ok) throw await failure(response);
  const body = (await response.json()) as { submissions: SubmissionSummary[] };
  return body.submissions;
}

export async function loadSubmission(caseId: string, username: string): Promise<LoadedSubmission> {
  const url = `${BASE}/${encodeURIComponent(caseId)}?${query(username)}`;
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw await failure(response);
  return parseExport(await response.text());
}

export async function saveUserSet(
  caseId: string,
  username: string,
  version: number,
  mapText: string,
): Promise<SaveResult> {
  const url = `${BASE}/${encodeURIComponent(caseId)}/annotation-sets/user?${query(username)}`;
  const response = await fetch(url, {
    method: "PUT",
    cache: "no-store",
    headers: { "Content-Type": "application/json", "If-Match": `"${version}"` },
    body: `{"annotations":${mapText}}`,
  });
  if (response.ok) {
    const body = (await response.json()) as { version: number; updated_at: string };
    return { kind: "saved", version: body.version, updatedAt: body.updated_at };
  }
  if (response.status === 412) return { kind: "conflict" };
  if (response.status === 422) {
    const body = (await response.json()) as {
      records?: unknown;
      issues: Issue[];
      issue_count: number;
      error?: { code?: string };
    };
    // A ValidationReport (it has records) describes the annotations; anything else is a failure.
    if (body.records !== undefined) {
      return { kind: "invalid", issues: body.issues, issueCount: body.issue_count };
    }
    throw new ApiError(422, body.error?.code ?? null);
  }
  throw await failure(response);
}

/** Removes one stored LLM set (stage 4 §4.6); the answer has the same shape as a save. */
export async function removeLlmSet(
  caseId: string,
  username: string,
  setId: string,
  version: number,
): Promise<SaveResult> {
  const url = `${BASE}/${encodeURIComponent(caseId)}/annotation-sets?${query(username)}&set_id=${encodeURIComponent(setId)}`;
  const response = await fetch(url, {
    method: "DELETE",
    cache: "no-store",
    headers: { "If-Match": `"${version}"` },
  });
  if (response.ok) {
    const body = (await response.json()) as { version: number; updated_at: string };
    return { kind: "saved", version: body.version, updatedAt: body.updated_at };
  }
  if (response.status === 412) return { kind: "conflict" };
  throw await failure(response);
}

/** What went wrong and what to do, in the interface's voice. */
export function describeError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "The service didn't respond. Check that it's running, then try again.";
  }
  switch (error.code) {
    case "STORAGE_UNAVAILABLE":
      return "The case store isn't reachable. Check that the database is running, then try again.";
    case "NOT_FOUND":
      return "This case isn't stored under this username. It may have been deleted.";
    case "SET_NOT_FOUND":
      return "That set is no longer stored. Reload the case.";
    case "INVALID_PARAMETERS":
      return "That username isn't valid. Use an ASCII handle or a single email address.";
    default:
      return `The request failed (${error.code ?? `HTTP ${error.status}`}). Try again.`;
  }
}

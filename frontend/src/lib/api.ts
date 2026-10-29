import { loadedAnnotation, type Annotation } from "./annotations";
import type { Label } from "../generated/labels";
import { parseExport, type LlmSet, type LoadedSubmission } from "./record";

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
    /** For `LLM_OUTPUT_INVALID`: why the model's output was rejected. */
    readonly reason: string | null = null,
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
    const body = (await response.json()) as { error?: { code?: string }; issues?: { code?: string }[] };
    const code = body.error?.code ?? null;
    const reason = code === "LLM_OUTPUT_INVALID" ? (body.issues?.[0]?.code ?? null) : null;
    return new ApiError(response.status, code, reason);
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

export interface LlmStatus {
  readonly enabled: boolean;
  readonly provider: string | null;
  readonly model: string | null;
}

const DISABLED: LlmStatus = { enabled: false, provider: null, model: null };

/** Whether LLM annotation is configured (stage 4 §4.1); any failure means "not available". */
export async function getLlmStatus(): Promise<LlmStatus> {
  try {
    const response = await fetch("/api/v1/llm", { cache: "no-store" });
    if (!response.ok) return DISABLED;
    const body = (await response.json()) as Partial<LlmStatus>;
    return {
      enabled: body.enabled === true,
      provider: body.provider ?? null,
      model: body.model ?? null,
    };
  } catch {
    return DISABLED;
  }
}

/** A model's annotations awaiting review; nothing is stored until it is accepted. */
export interface Proposal {
  readonly proposalId: string;
  readonly set: LlmSet;
  readonly countsByLabel: Readonly<Record<string, number>>;
  readonly count: number;
  readonly replacesExistingSet: boolean;
  readonly expiresAt: string;
}

interface ProposalBody {
  proposal_id: string;
  set_id: string;
  set: {
    provider: string;
    model: string;
    generated_at: string;
    annotations: Record<string, { text: string; start: number; end: number }[]>;
  };
  annotation_count: number;
  annotations_by_label: Record<string, number>;
  replaces_existing_set: boolean;
  expires_at: string;
}

function proposalUrl(caseId: string, username: string, proposalId?: string, action?: string): string {
  const base = `${BASE}/${encodeURIComponent(caseId)}/llm-proposals`;
  const path = proposalId === undefined ? base : `${base}/${encodeURIComponent(proposalId)}`;
  return `${path}${action === undefined ? "" : `/${action}`}?${query(username)}`;
}

/** Asks the configured model to annotate the stored narrative (stage 4 §4.3). */
export async function proposeAnnotations(
  caseId: string,
  username: string,
  signal: AbortSignal,
): Promise<Proposal> {
  const response = await fetch(proposalUrl(caseId, username), {
    method: "POST",
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw await failure(response);
  const body = (await response.json()) as ProposalBody;
  const annotations: Annotation[] = [];
  let position = 0;
  for (const [label, entries] of Object.entries(body.set.annotations)) {
    for (const entry of entries) {
      annotations.push(
        // Read-only: a proposal is never sent back, so no source text is kept.
        loadedAnnotation(label as Label, entry.start, entry.end, entry.text, undefined, position),
      );
      position += 1;
    }
  }
  return {
    proposalId: body.proposal_id,
    set: {
      id: body.set_id,
      provider: body.set.provider,
      model: body.set.model,
      generatedAt: body.set.generated_at,
      annotations,
    },
    countsByLabel: body.annotations_by_label,
    count: body.annotation_count,
    replacesExistingSet: body.replaces_existing_set,
    expiresAt: body.expires_at,
  };
}

/** Stores a proposal as a new version (stage 4 §4.4); the answer has the shape of a save. */
export async function acceptProposal(
  caseId: string,
  username: string,
  proposalId: string,
  version: number,
): Promise<SaveResult> {
  const response = await fetch(proposalUrl(caseId, username, proposalId, "accept"), {
    method: "POST",
    cache: "no-store",
    headers: { "If-Match": `"${version}"` },
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
    if (body.records !== undefined) {
      return { kind: "invalid", issues: body.issues, issueCount: body.issue_count };
    }
    throw new ApiError(422, body.error?.code ?? null);
  }
  throw await failure(response);
}

/** Drops a proposal (stage 4 §4.5); one that is already gone is not a problem. */
export async function discardProposal(
  caseId: string,
  username: string,
  proposalId: string,
): Promise<void> {
  const response = await fetch(proposalUrl(caseId, username, proposalId), {
    method: "DELETE",
    cache: "no-store",
  });
  if (!response.ok && response.status !== 404) throw await failure(response);
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
    case "LLM_NOT_CONFIGURED":
      return "LLM annotation isn't configured on this server.";
    case "LLM_TIMEOUT":
      return "The model took too long to answer. Try again, or use a shorter narrative.";
    case "LLM_UNAVAILABLE":
      return "The model service didn't answer. Check the provider settings, then try again.";
    case "LLM_OUTPUT_INVALID":
      return error.reason === "NARRATIVE_ALTERED"
        ? "The model changed the narrative text, so its annotations can't be used. Running it again may help."
        : `The model's annotations were malformed (${error.reason ?? "unknown"}). Running it again may help.`;
    case "NARRATIVE_HAS_MARKUP":
      return "This narrative already contains annotation tags, so it can't be annotated by the model.";
    case "NARRATIVE_TOO_LONG":
      return "This narrative is too long for LLM annotation.";
    case "PROPOSAL_NOT_FOUND":
      return "This proposal has expired. Run the model again.";
    case "INVALID_PARAMETERS":
      return "That username isn't valid. Use an ASCII handle or a single email address.";
    default:
      return `The request failed (${error.code ?? `HTTP ${error.status}`}). Try again.`;
  }
}

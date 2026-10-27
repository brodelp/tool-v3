import { isLabel, loadedAnnotation, type Annotation } from "./annotations";
import { arrayElements, member, objectMembers } from "./jsonSpans";
import { NarrativeIndex } from "./offsets";

/** A stored submission opened for annotation. */
export interface LoadedSubmission {
  readonly caseId: string;
  readonly username: string;
  readonly version: number;
  readonly submittedAt: string;
  readonly updatedAt: string;
  readonly narrative: NarrativeIndex;
  readonly annotations: readonly Annotation[];
}

interface ExportHead {
  case_id: string;
  username: string;
  version: number;
  submitted_at: string;
  updated_at: string;
  record: { narrative: string };
}

interface AnnotationValues {
  text: string;
  start: number;
  end: number;
}

/**
 * Reads an export body (stage 3 §3.3). Field values come from `JSON.parse`; each annotation
 * also keeps its exact source text, which `JSON.parse` alone would lose (number tokens).
 */
export function parseExport(body: string): LoadedSubmission {
  const head = JSON.parse(body) as ExportHead;
  const top = objectMembers(body, body.search(/\S/));
  const record = member(top, "record");
  if (record === undefined) throw new SyntaxError("export has no record");
  const map = member(objectMembers(body, record.start), "annotations");
  const narrative = new NarrativeIndex(head.record.narrative);
  const annotations: Annotation[] = [];
  if (map !== undefined) {
    for (const entry of objectMembers(body, map.start)) {
      const label = entry.name;
      if (!isLabel(label)) throw new SyntaxError("export has an unknown label");
      arrayElements(body, entry.start).forEach((span, position) => {
        const source = body.slice(span.start, span.end);
        const values = JSON.parse(source) as AnnotationValues;
        annotations.push(
          loadedAnnotation(label, values.start, values.end, values.text, source, position),
        );
      });
    }
  }
  return {
    caseId: head.case_id,
    username: head.username,
    version: head.version,
    submittedAt: head.submitted_at,
    updatedAt: head.updated_at,
    narrative,
    annotations,
  };
}

import { describe, expect, it } from "vitest";

import {
  assignLanes,
  byPosition,
  isDuplicate,
  newAnnotation,
  serializeMap,
  type Annotation,
} from "./annotations";
import { NarrativeIndex } from "./offsets";
import { parseExport } from "./record";

const index = new NarrativeIndex("Aspirin caused nausea.");

describe("newAnnotation", () => {
  it("quotes exactly the selected code points", () => {
    const a = newAnnotation(index, "AE", 15, 21);
    expect(a.text).toBe("nausea");
    expect(newAnnotation(index, "Dose", 7, 15).text).toBe(" caused ");
  });

  it("uses code points for astral narratives", () => {
    const astral = new NarrativeIndex("😀 rash");
    expect(newAnnotation(astral, "AE", 2, 6).text).toBe("rash");
  });

  it("rejects empty and out-of-range spans", () => {
    expect(() => newAnnotation(index, "AE", 3, 3)).toThrow(RangeError);
    expect(() => newAnnotation(index, "AE", 0, 23)).toThrow(RangeError);
  });
});

describe("isDuplicate", () => {
  it("compares label and range only", () => {
    const list = [newAnnotation(index, "AE", 15, 21)];
    expect(isDuplicate(list, "AE", 15, 21)).toBe(true);
    expect(isDuplicate(list, "mAE", 15, 21)).toBe(false);
    expect(isDuplicate(list, "AE", 15, 20)).toBe(false);
    expect(isDuplicate(list, "AE", 15, 21, list[0]?.id)).toBe(false);
  });
});

describe("serializeMap (F11)", () => {
  it("orders labels canonically and new spans by position", () => {
    const list: Annotation[] = [
      newAnnotation(index, "AE", 15, 21),
      newAnnotation(index, "SDrug", 0, 7),
      newAnnotation(index, "AE", 0, 7),
    ];
    const { text, positions } = serializeMap(list);
    expect(text).toBe(
      '{"SDrug":[{"text":"Aspirin","start":0,"end":7}],' +
        '"AE":[{"text":"Aspirin","start":0,"end":7},{"text":"nausea","start":15,"end":21}]}',
    );
    expect(positions.get("AE/1")).toBe(list[0]?.id);
  });

  it("serializes no annotations as an empty map", () => {
    expect(serializeMap([]).text).toBe("{}");
  });

  it("round-trips loaded annotations byte for byte, after new ones are added", () => {
    const sdrug = '{ "external_id":"ann-001", "text":"Aspirin","start":0,"end":7, "w":1.0E2 }';
    const ae = '{"text":"nausea","start":15,"end":21,"big":12345678901234567890}';
    const body =
      '{"case_id":"5001-1","username":"demo_annotator","submitted_at":"t","updated_at":"t",' +
      '"version":3,"offset_unit":"unicode-code-point","record":{"case_id":"5001-1",' +
      '"narrative":"Aspirin caused nausea.","annotations":{"AE":[' +
      ae +
      '],"SDrug":[\n  ' +
      sdrug +
      "\n]},\"x\":1.2300}}";
    const loaded = parseExport(body);
    expect(loaded.version).toBe(3);
    expect(loaded.narrative.text).toBe("Aspirin caused nausea.");
    expect(loaded.annotations.map((a) => [a.label, a.text])).toEqual([
      ["AE", "nausea"],
      ["SDrug", "Aspirin"],
    ]);
    const extra = newAnnotation(loaded.narrative, "AE", 8, 14);
    const { text } = serializeMap([extra, ...loaded.annotations]);
    expect(text).toBe(
      `{"SDrug":[${sdrug}],"AE":[${ae},{"text":"caused","start":8,"end":14}]}`,
    );
  });

  it("keeps a relabelled loaded annotation's source", () => {
    const body =
      '{"version":1,"record":{"narrative":"Aspirin","annotations":' +
      '{"SDrug":[{"text":"Aspirin","start":0,"end":7,"id":"x"}]}}}';
    const [first] = parseExport(body).annotations;
    if (first === undefined) throw new Error("missing annotation");
    const relabelled: Annotation = { ...first, label: "CDrug" };
    expect(serializeMap([relabelled]).text).toBe(
      '{"CDrug":[{"text":"Aspirin","start":0,"end":7,"id":"x"}]}',
    );
  });

  it("reads a record without annotations", () => {
    const body = '{"version":1,"record":{"narrative":"n","annotations_note":{"a":[]}}}';
    expect(parseExport(body).annotations).toEqual([]);
  });
});

describe("assignLanes", () => {
  it("never puts overlapping spans in one lane", () => {
    const list = [
      newAnnotation(index, "Temporal", 0, 22),
      newAnnotation(index, "SDrug", 0, 7),
      newAnnotation(index, "AE", 15, 21),
      newAnnotation(index, "Dose", 5, 16),
    ];
    const lanes = assignLanes(list);
    for (const a of list) {
      for (const b of list) {
        const overlap = a.id !== b.id && a.start < b.end && b.start < a.end;
        if (overlap) expect(lanes.get(a.id)).not.toBe(lanes.get(b.id));
      }
    }
    expect(lanes.get(list[0]?.id ?? "")).toBe(0);
  });

  it("sorts by start, longer first", () => {
    const list = [newAnnotation(index, "AE", 0, 3), newAnnotation(index, "AE", 0, 7)];
    expect([...list].sort(byPosition).map((a) => a.end)).toEqual([7, 3]);
  });
});

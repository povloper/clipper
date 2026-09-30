import { describe, expect, it } from "vitest";
import {
  addColdOpen,
  captionGroups,
  editDuration,
  frameRect,
  mappedWords,
  outputSize,
  removeColdOpen,
  replaceWords,
  splitSegment,
} from "./document";
import { draft } from "./document.test-fixture";

describe("non-destructive edits", () => {
  it("adds and removes the cold open as a separate leading segment", () => {
    const main = {
      ...draft,
      segments: [{ id: "original", start: 1.5, end: 4.5 }],
    };
    const withHook = addColdOpen(main, { start: 0, end: 1.5 });

    expect(withHook.segments).toEqual([
      { id: "cold-open", start: 0, end: 1.5 },
      { id: "original", start: 1.5, end: 4.5 },
    ]);
    expect(editDuration(withHook)).toBeCloseTo(4.5);
    expect(addColdOpen(withHook, { start: 0, end: 1.5 })).toBe(withHook);

    const withoutHook = removeColdOpen(withHook);
    expect(withoutHook.segments).toEqual(main.segments);
    expect(removeColdOpen(withoutHook)).toBe(withoutHook);
    expect(main.segments).toEqual([
      { id: "original", start: 1.5, end: 4.5 },
    ]);
  });
  it("splits without changing duration or original state", () => {
    const next = splitSegment(draft, 0, 1.1);
    expect(next.segments).toHaveLength(2);
    expect(editDuration(next)).toBeCloseTo(3);
    expect(draft.segments).toHaveLength(1);
    expect(splitSegment(draft, 0, 0.01)).toBe(draft);
  });
  it("maps captions through reordered and duplicated cuts", () => {
    const doc = {
      ...draft,
      segments: [
        { id: "a", start: 1, end: 2 },
        { id: "b", start: 0, end: 1 },
      ],
    };
    const words = mappedWords(doc);
    expect(words.map((w) => [w.text, w.start, w.end])).toEqual([
      ["world", 0, 0.5],
      ["Hello", 1, 1.5],
    ]);
    expect(words[0].highlight).toBe(true);
    expect(captionGroups(words, 1)).toHaveLength(2);
  });
  it("retains downstream word timings after a phrase correction", () => {
    const next = replaceWords(draft.words, "Hello beautiful world today", 3);
    expect(next.find((w) => w.text === "world")).toEqual(draft.words[1]);
    expect(next.find((w) => w.text === "today")).toEqual(draft.words[2]);
    expect(next.every((w) => w.end > w.start && w.end <= 3)).toBe(true);
    expect(replaceWords(draft.words, "Hello earth today", 3)[1]).toMatchObject({
      text: "earth",
      start: 1,
      end: 1.5,
    });
  });
  it("positions crop anchors and preserves original dimensions", () => {
    expect(
      frameRect({ ...draft.framing, x: 0 }, 1920, 1080, 1080, 1920).x,
    ).toBeCloseTo(0);
    const right = frameRect({ ...draft.framing, x: 1 }, 1920, 1080, 1080, 1920);
    expect(right.x + right.width).toBe(1080);
    expect(
      outputSize(
        { ...draft, framing: { ...draft.framing, aspect: "original" } },
        1280,
        720,
      ),
    ).toEqual({ width: 1280, height: 720 });
  });
});

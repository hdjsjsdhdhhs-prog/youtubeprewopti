import { describe, expect, it } from "vitest";

import { LEAD_STATUSES, leadQueryFromParams } from "./leads";
import {
  EMPTY_SELECTION,
  MANUAL_STATUSES,
  actionLabel,
  filtersFromQuery,
  humanValue,
  isSelected,
  manualTargets,
  parseScore,
  selectedCount,
  selectionBody,
  tagStyle,
  toggleOne,
  togglePage,
  type ScoreOverride,
} from "./workflow";

describe("manual statuses", () => {
  it("mirrors the manual part of the lead state machine", () => {
    expect(manualTargets("analyzed")).toEqual(["shortlisted", "selected", "rejected"]);
    expect(manualTargets("shortlisted")).toEqual(["selected", "analyzed", "rejected"]);
    expect(manualTargets("rejected")).toEqual(["shortlisted"]);
    expect(manualTargets("sent")).toEqual([]);
    // every target is a manual status
    for (const s of LEAD_STATUSES) {
      for (const t of manualTargets(s)) expect(MANUAL_STATUSES).toContain(t);
    }
  });

  it("labels a transition by where the lead comes from", () => {
    expect(actionLabel("analyzed", "shortlisted")).toBe("В шорт-лист");
    expect(actionLabel("rejected", "shortlisted")).toBe("Восстановить");
    expect(actionLabel("selected", "shortlisted")).toBe("Вернуть в шорт-лист");
    expect(actionLabel("shortlisted", "analyzed")).toBe("Убрать из шорт-листа");
  });
});

describe("selection (ТЗ §11)", () => {
  it("toggles single rows and the page", () => {
    let s = toggleOne(EMPTY_SELECTION, 1);
    expect(isSelected(s, 1)).toBe(true);
    s = togglePage(s, [1, 2, 3]);
    expect([1, 2, 3].every((id) => isSelected(s, id))).toBe(true);
    s = togglePage(s, [1, 2, 3]);
    expect(selectedCount(s, 100)).toBe(0);
  });

  it("«all filtered» minus unticked rows", () => {
    let s = toggleOne({ mode: "all", excluded: new Set() }, 7);
    expect(isSelected(s, 7)).toBe(false);
    expect(isSelected(s, 8)).toBe(true);
    expect(selectedCount(s, 120)).toBe(119);
    s = togglePage(s, [7, 8]); // not all on => tick all
    expect(selectedCount(s, 120)).toBe(120);
  });

  it("builds the API selection from the list query without paging / sorting", () => {
    const q = leadQueryFromParams(new URLSearchParams("status=analyzed&tag=4,5&favorite=true&sort=priority&offset=50"));
    expect(q).toMatchObject({ tag: [4, 5], favorite: true });
    expect(filtersFromQuery(q)).not.toHaveProperty("sort");
    expect(filtersFromQuery(q)).not.toHaveProperty("offset");
    expect(selectionBody({ mode: "all", excluded: new Set([9]) }, q)).toEqual({
      filters: expect.objectContaining({ status: ["analyzed"], tag: [4, 5], favorite: true }),
      exclude_ids: [9],
    });
    expect(selectionBody(toggleOne(EMPTY_SELECTION, 3), q)).toEqual({ lead_ids: [3] });
    expect(leadQueryFromParams(new URLSearchParams("favorite=maybe")).favorite).toBeUndefined();
    expect(leadQueryFromParams(new URLSearchParams("favorite=false")).favorite).toBe(false);
  });
});

describe("human scores and tags", () => {
  it("parses a typed score within the range", () => {
    expect(parseScore("5,5", 1, 10)).toBe(5.5);
    expect(parseScore(" 10 ", 1, 10)).toBe(10);
    expect(parseScore("0.5", 1, 10)).toBeNull();
    expect(parseScore("5.555", 1, 10)).toBeNull();
    expect(parseScore("abc", 0, 100)).toBeNull();
    expect(parseScore("100", 0, 100)).toBe(100);
  });

  it("finds the human value of a field", () => {
    const o = { id: 1, field: "total", human_value: "55.00" } as ScoreOverride;
    expect(humanValue([o], "total")).toBe(o);
    expect(humanValue([o], "priority_score")).toBeUndefined();
    expect(humanValue(undefined, "total")).toBeUndefined();
  });

  it("tints a tag with its color, ignores invalid colors", () => {
    expect(tagStyle("#ff0000")).toEqual({ backgroundColor: "#ff000026", borderColor: "#ff0000" });
    expect(tagStyle("red")).toEqual({});
    expect(tagStyle(null)).toEqual({});
  });
});

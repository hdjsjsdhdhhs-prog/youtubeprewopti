import { describe, expect, it } from "vitest";

import {
  CLEAR_FILTERS,
  activeFilterCount,
  filterSetFromFilters,
  filtersFromParams,
  patchFromFilterSet,
} from "@/app/(app)/channels/channel-filters";

import type { TaxonomyNode } from "./api/hooks";
import { formatRatio } from "./format";
import { taxonomyOptions, taxonomyPath } from "./taxonomy";

describe("§3 channel filters in the URL", () => {
  it("parses metric, activity, niche and market filters", () => {
    const f = filtersFromParams(
      new URLSearchParams(
        "min_avg_views=10000&max_days_since_last_upload=14&min_videos_30d=2&min_views_to_subs=0.25" +
          "&min_upload_consistency=0.5&niche=3,4,3&exclude_niche=9&language=ru&sort=avg_views",
      ),
    );
    expect(f).toMatchObject({
      min_avg_views: 10000,
      max_days_since_last_upload: 14,
      min_videos_30d: 2,
      min_views_to_subs: 0.25,
      min_upload_consistency: 0.5,
      niche: [3, 4],
      exclude_niche: [9],
      language: "ru",
      sort: "avg_views",
    });
    expect(activeFilterCount(f)).toBe(8);
  });

  it("drops values the API would reject", () => {
    const f = filtersFromParams(
      new URLSearchParams("min_upload_consistency=1.5&min_views_to_subs=-1&niche=a,0,-2&min_avg_views=1.5"),
    );
    expect(f.min_upload_consistency).toBeUndefined();
    expect(f.min_views_to_subs).toBeUndefined();
    expect(f.niche).toBeUndefined();
    expect(f.min_avg_views).toBeUndefined();
    expect(activeFilterCount(f)).toBe(0);
  });

  it("round-trips through the project's saved filter_settings", () => {
    const f = filtersFromParams(new URLSearchParams("min_subscribers=5000&niche=3,4&q=ignored&project_id=7"));
    const saved = filterSetFromFilters(f);
    expect(saved).toEqual({ min_subscribers: 5000, niche: [3, 4] }); // search/project are not part of the set
    const patch = patchFromFilterSet(saved as Record<string, unknown>);
    expect(patch).toMatchObject({ min_subscribers: 5000, niche: "3,4", max_subscribers: null, country: null });
    expect(Object.keys(patch).every((k) => k in CLEAR_FILTERS)).toBe(true);
  });
});

const node = (id: number, name: string, parent_id: number | null, level: TaxonomyNode["level"]): TaxonomyNode => ({
  id,
  name,
  parent_id,
  level,
  slug: name.toLowerCase(),
});

describe("taxonomy helpers", () => {
  const nodes = [
    node(3, "Investing", 1, "topic"),
    node(2, "Gaming", null, "niche"),
    node(1, "Finance", null, "niche"),
    node(4, "ETF", 3, "subtopic"),
  ];

  it("orders nodes as a tree with indentation", () => {
    expect(taxonomyOptions(nodes).map((o) => [o.id, o.depth])).toEqual([
      [1, 0],
      [3, 1],
      [4, 2],
      [2, 0],
    ]);
  });

  it("builds the path of a node", () => {
    expect(taxonomyPath(nodes, 4)).toBe("Finance › Investing › ETF");
    expect(taxonomyPath(nodes, null)).toBeNull();
    expect(taxonomyPath(nodes, 999)).toBeNull();
  });

  it("formats ratios", () => {
    expect(formatRatio(0.553)).toBe("0,55");
    expect(formatRatio(12.44)).toBe("12,4");
    expect(formatRatio(null)).toBe("—");
  });
});

import type { Metadata } from "next";

import { TagsView } from "./tags-view";

export const metadata: Metadata = { title: "Теги" };

export default function TagsPage() {
  return <TagsView />;
}

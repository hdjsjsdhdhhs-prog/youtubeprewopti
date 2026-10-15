import type { Metadata } from "next";

import { ReferencesView } from "./references-view";

export const metadata: Metadata = { title: "Референсы" };

export default function ReferencesPage() {
  return <ReferencesView />;
}

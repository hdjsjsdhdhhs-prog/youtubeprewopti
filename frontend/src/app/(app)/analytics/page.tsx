import type { Metadata } from "next";
import { Suspense } from "react";

import { AnalyticsView } from "./analytics-view";

export const metadata: Metadata = { title: "Аналитика" };

export default function AnalyticsPage() {
  return (
    <Suspense>
      <AnalyticsView />
    </Suspense>
  );
}

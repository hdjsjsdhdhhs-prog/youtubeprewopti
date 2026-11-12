import type { Metadata } from "next";
import { Suspense } from "react";

import { DashboardView } from "./dashboard-view";

export const metadata: Metadata = { title: "Дашборд" };

export default function DashboardPage() {
  return (
    <Suspense>
      <DashboardView />
    </Suspense>
  );
}

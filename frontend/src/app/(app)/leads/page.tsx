import type { Metadata } from "next";
import { Suspense } from "react";

import { LeadsView } from "./leads-view";

export const metadata: Metadata = { title: "Лиды" };

export default function LeadsPage() {
  return (
    <Suspense>
      <LeadsView />
    </Suspense>
  );
}

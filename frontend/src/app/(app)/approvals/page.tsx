import type { Metadata } from "next";
import { Suspense } from "react";

import { ApprovalsView } from "./approvals-view";

export const metadata: Metadata = { title: "Очередь подтверждения" };

export default function ApprovalsPage() {
  return (
    <Suspense>
      <ApprovalsView />
    </Suspense>
  );
}

import type { Metadata } from "next";

import { ApprovalsView } from "./approvals-view";

export const metadata: Metadata = { title: "Очередь подтверждения" };

export default function ApprovalsPage() {
  return <ApprovalsView />;
}

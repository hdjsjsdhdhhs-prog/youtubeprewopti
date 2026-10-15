import type { Metadata } from "next";

import { SuppressionView } from "./suppression-view";

export const metadata: Metadata = { title: "Не писать" };

export default function SuppressionPage() {
  return <SuppressionView />;
}

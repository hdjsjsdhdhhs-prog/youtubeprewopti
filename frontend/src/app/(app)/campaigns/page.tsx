import type { Metadata } from "next";

import { CampaignsView } from "./campaigns-view";

export const metadata: Metadata = { title: "Кампании" };

export default function CampaignsPage() {
  return <CampaignsView />;
}

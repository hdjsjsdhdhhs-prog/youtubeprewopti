import type { Metadata } from "next";

import { OfferTemplatesView } from "./offer-templates-view";

export const metadata: Metadata = { title: "Шаблоны офферов" };

export default function OfferTemplatesPage() {
  return <OfferTemplatesView />;
}

import type { Metadata } from "next";

import { NichesView } from "./niches-view";

export const metadata: Metadata = { title: "Ниши" };

export default function NichesPage() {
  return <NichesView />;
}

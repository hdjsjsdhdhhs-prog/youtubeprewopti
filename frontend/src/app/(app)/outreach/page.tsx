import type { Metadata } from "next";

import { OutreachView } from "./outreach-view";

export const metadata: Metadata = { title: "Рассылка" };

export default function OutreachPage() {
  return <OutreachView />;
}

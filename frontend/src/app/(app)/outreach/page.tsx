import type { Metadata } from "next";
import { Suspense } from "react";

import { OutreachView } from "./outreach-view";

export const metadata: Metadata = { title: "Рассылка" };

export default function OutreachPage() {
  return (
    <Suspense>
      <OutreachView />
    </Suspense>
  );
}

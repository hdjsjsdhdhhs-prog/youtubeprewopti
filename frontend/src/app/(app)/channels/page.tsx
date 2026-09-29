import type { Metadata } from "next";
import { Suspense } from "react";

import { ChannelsView } from "./channels-view";

export const metadata: Metadata = { title: "Каналы" };

export default function ChannelsPage() {
  return (
    <Suspense>
      <ChannelsView />
    </Suspense>
  );
}

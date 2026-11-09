"use client";

import { useParams } from "next/navigation";

import { EmptyState } from "@/components/states";

import { CampaignDetail } from "./campaign-detail";

export default function CampaignPage() {
  const { id: rawId } = useParams<{ id: string }>();
  const id = Number(rawId);
  if (!Number.isInteger(id) || id <= 0) return <EmptyState title="Кампания не найдена" />;
  return <CampaignDetail id={id} />;
}

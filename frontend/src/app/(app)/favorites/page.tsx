import type { Metadata } from "next";

import { FavoritesView } from "./favorites-view";

export const metadata: Metadata = { title: "Избранное" };

export default function FavoritesPage() {
  return <FavoritesView />;
}

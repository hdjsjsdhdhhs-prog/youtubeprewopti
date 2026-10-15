import type { Metadata } from "next";

import { EmailAccountsView } from "./email-accounts-view";

export const metadata: Metadata = { title: "Почтовые аккаунты" };

export default function EmailAccountsPage() {
  return <EmailAccountsView />;
}

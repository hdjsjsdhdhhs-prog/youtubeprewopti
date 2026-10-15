import { describe, expect, it } from "vitest";

import { offerNeedsPolling } from "./offers";
import {
  DEFAULT_ACCOUNT_FORM,
  accountCreateRequest,
  accountFormErrors,
  accountPatchRequest,
  canCancelMessage,
  formatCooldown,
  isMessageActive,
  type AccountForm,
} from "./outreach";

const form = (over: Partial<AccountForm> = {}): AccountForm => ({
  ...DEFAULT_ACCOUNT_FORM,
  name: " Main ",
  from_email: "me@example.com",
  smtp_host: "smtp.example.com",
  ...over,
});

describe("account form", () => {
  it("omits an empty password and opt-out text so the backend keeps its defaults", () => {
    expect(accountCreateRequest(form())).toEqual({
      name: "Main",
      from_email: "me@example.com",
      from_name: "",
      reply_to: null,
      smtp_host: "smtp.example.com",
      smtp_port: 587,
      smtp_security: "starttls",
      smtp_username: "",
      daily_limit: 20,
      cooldown_min_seconds: 60,
      cooldown_max_seconds: 180,
    });
    const full = accountCreateRequest(form({ password: "s3cret ", opt_out_text: " Ответьте «нет». ", reply_to: "r@example.com" }));
    expect(full.password).toBe("s3cret "); // passwords are not trimmed
    expect(full.opt_out_text).toBe("Ответьте «нет».");
    expect(full.reply_to).toBe("r@example.com");
  });

  it("patches only the changed fields; the password only when a new one is typed", () => {
    const saved = form({ opt_out_text: "Ответьте «нет»." });
    expect(accountPatchRequest(saved, { ...saved })).toEqual({});
    expect(accountPatchRequest(saved, { ...saved, daily_limit: 50, reply_to: "r@example.com" })).toEqual({
      daily_limit: 50,
      reply_to: "r@example.com",
    });
    expect(accountPatchRequest({ ...saved, reply_to: "r@example.com" }, saved)).toEqual({ reply_to: null });
    expect(accountPatchRequest(saved, { ...saved, password: "new" })).toEqual({ password: "new" });
  });

  it("validates like the backend", () => {
    expect(accountFormErrors(form())).toEqual([]);
    expect(accountFormErrors(form({ name: " ", from_email: "nope", smtp_host: "" }))).toHaveLength(3);
    expect(accountFormErrors(form({ smtp_port: 0 }))).toHaveLength(1);
    expect(accountFormErrors(form({ daily_limit: 501 }))).toHaveLength(1);
    expect(accountFormErrors(form({ daily_limit: Number.NaN }))).toHaveLength(1);
    expect(accountFormErrors(form({ cooldown_min_seconds: 200, cooldown_max_seconds: 100 }))).toEqual([
      "Максимальная пауза не может быть меньше минимальной.",
    ]);
    expect(accountFormErrors(form({ reply_to: "bad" }))).toHaveLength(1);
    // the opt-out text may be left empty only on create (backend default)
    expect(accountFormErrors(form({ opt_out_text: "" }), { editing: true })).toHaveLength(1);
  });
});

describe("messages", () => {
  it("knows which statuses change by themselves and which can be cancelled", () => {
    expect(["queued", "sending", "sent", "manual"].map((s) => isMessageActive(s as never))).toEqual([true, true, false, false]);
    expect(["queued", "manual", "sending", "sent", "failed"].map((s) => canCancelMessage(s as never))).toEqual([
      true,
      true,
      false,
      false,
      false,
    ]);
  });

  it("formats the cooldown", () => {
    expect(formatCooldown(60, 180)).toBe("1–3 мин");
    expect(formatCooldown(30, 90)).toBe("30–90 с");
    expect(formatCooldown(0, 0)).toBe("0 с");
  });

  it("polls an offer only while its message is due or being sent", () => {
    const now = Date.parse("2026-10-15T12:00:00Z");
    const o = (message: { status: string; scheduled_at: string; next_attempt_at: string | null } | null) =>
      ({ status: "scheduled", job: null, message }) as const;
    expect(offerNeedsPolling(o(null), now)).toBe(false);
    expect(offerNeedsPolling(o({ status: "queued", scheduled_at: "2026-10-16T10:00:00Z", next_attempt_at: null }), now)).toBe(false);
    expect(offerNeedsPolling(o({ status: "queued", scheduled_at: "2026-10-15T11:59:00Z", next_attempt_at: null }), now)).toBe(true);
    // a retry waits for its backoff
    expect(
      offerNeedsPolling(o({ status: "queued", scheduled_at: "2026-10-15T11:00:00Z", next_attempt_at: "2026-10-15T13:00:00Z" }), now),
    ).toBe(false);
    expect(offerNeedsPolling(o({ status: "sending", scheduled_at: "2026-10-16T10:00:00Z", next_attempt_at: null }), now)).toBe(true);
    expect(offerNeedsPolling(o({ status: "manual", scheduled_at: "2026-10-15T11:00:00Z", next_attempt_at: null }), now)).toBe(false);
  });
});

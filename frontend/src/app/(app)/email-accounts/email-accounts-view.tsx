"use client";

import { Pause, Pencil, Play, PlugZap, Plus } from "lucide-react";
import { useState } from "react";

import { EmptyState, ErrorState, InlineError, LoadingState, errorMessage } from "@/components/states";
import { Badge, Button, Card, Input, Label, PageHeader, Select, Textarea } from "@/components/ui";
import { useMe } from "@/lib/api/hooks";
import { offerErrorMessage } from "@/lib/offers";
import {
  useEmailAccounts,
  useSaveEmailAccount,
  useSetEmailAccountPaused,
  useVerifyEmailAccount,
  type EmailAccount,
} from "@/lib/api/outreach-hooks";
import { formatDateTime } from "@/lib/format";
import {
  ACCOUNT_STATUS_LABELS,
  ACCOUNT_STATUS_TONES,
  DEFAULT_ACCOUNT_FORM,
  DEFAULT_PORTS,
  MAX_COOLDOWN_SECONDS,
  MAX_DAILY_LIMIT,
  MAX_OPT_OUT,
  SMTP_SECURITY_LABELS,
  accountCreateRequest,
  accountForm,
  accountFormErrors,
  accountPatchRequest,
  formatCooldown,
  type AccountForm,
  type SmtpSecurity,
} from "@/lib/outreach";

/** ТЗ §27: SMTP accounts of the workspace — the password goes to the encrypted secret store and is never shown again;
 * daily limit, randomised pause between emails and the opt-out text are enforced on every send. Admins edit. */
export function EmailAccountsView() {
  const me = useMe();
  const isAdmin = me.data ? ["owner", "admin"].includes(me.data.role) : false;
  const accounts = useEmailAccounts();
  const [adding, setAdding] = useState(false);

  return (
    <div className="max-w-4xl">
      <PageHeader
        title="Почтовые аккаунты"
        subtitle="SMTP-аккаунты, с которых уходят письма: лимит в день, пауза между письмами, текст отказа от рассылки"
        actions={
          isAdmin && !adding ? (
            <Button onClick={() => setAdding(true)}>
              <Plus className="size-4" aria-hidden /> Добавить аккаунт
            </Button>
          ) : null
        }
      />
      {!isAdmin && me.data ? (
        <p className="mb-3 text-sm text-zinc-500">Добавлять и менять аккаунты может администратор workspace.</p>
      ) : null}
      {adding ? (
        <Card className="mb-4">
          <h2 className="mb-2 text-sm font-semibold">Новый аккаунт</h2>
          <AccountFormView onDone={() => setAdding(false)} />
        </Card>
      ) : null}
      {accounts.isPending ? (
        <LoadingState />
      ) : accounts.isError ? (
        <ErrorState error={accounts.error} onRetry={() => void accounts.refetch()} />
      ) : accounts.data.length === 0 ? (
        <EmptyState
          title="Аккаунтов пока нет"
          hint="Без активного SMTP-аккаунта письма не отправляются. Telegram, VK и ручной канал работают и без него — их вы отправляете сами."
        />
      ) : (
        <div className="space-y-3">
          {accounts.data.map((a) => (
            <AccountCard key={a.id} account={a} isAdmin={isAdmin} />
          ))}
        </div>
      )}
    </div>
  );
}

function AccountCard({ account: a, isAdmin }: { account: EmailAccount; isAdmin: boolean }) {
  const verify = useVerifyEmailAccount();
  const paused = useSetEmailAccountPaused();
  const [editing, setEditing] = useState(false);
  const check = verify.data;

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-semibold">{a.name}</h2>
        <Badge tone={ACCOUNT_STATUS_TONES[a.status]}>{ACCOUNT_STATUS_LABELS[a.status]}</Badge>
        <span className="text-sm text-zinc-600 dark:text-zinc-400">
          {a.from_name ? `${a.from_name} <${a.from_email}>` : a.from_email}
        </span>
      </div>
      {a.paused_reason ? <p className="mt-1 text-sm text-amber-700 dark:text-amber-400">{a.paused_reason}</p> : null}
      <dl className="mt-2 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs text-zinc-500">Сервер</dt>
          <dd>
            {a.smtp_host}:{a.smtp_port} · {SMTP_SECURITY_LABELS[a.smtp_security]}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-zinc-500">Пароль</dt>
          <dd>{a.password_configured ? (a.password_hint ?? "задан") : "не задан"}</dd>
        </div>
        <div>
          <dt className="text-xs text-zinc-500">Сегодня (UTC)</dt>
          <dd>
            {a.sent_today} из {a.daily_limit} · осталось {a.remaining_today}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-zinc-500">Пауза между письмами</dt>
          <dd>
            {formatCooldown(a.cooldown_min_seconds, a.cooldown_max_seconds)}
            {a.next_send_at ? ` · следующее не раньше ${formatDateTime(a.next_send_at)}` : ""}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-zinc-500">Ответы · отказы · недоставки</dt>
          <dd>
            {a.replies} · {a.opt_outs} · {a.bounces}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-zinc-500">Последнее письмо</dt>
          <dd>{formatDateTime(a.last_sent_at)}</dd>
        </div>
      </dl>
      {a.last_error_message ? (
        <p className="mt-2 text-sm text-red-700 dark:text-red-400">
          Последняя ошибка{a.consecutive_failures ? ` (подряд: ${a.consecutive_failures})` : ""}: {a.last_error_message}
          {a.last_error_code ? <span className="ml-1 font-mono text-xs opacity-70">{a.last_error_code}</span> : null}
        </p>
      ) : null}
      {check ? (
        <p role="status" className={check.ok ? "mt-2 text-sm text-emerald-700 dark:text-emerald-400" : "mt-2 text-sm text-red-700 dark:text-red-400"}>
          {check.message}
        </p>
      ) : null}
      {isAdmin ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" disabled={verify.isPending} onClick={() => verify.mutate(a.id)}>
            <PlugZap className="size-3.5" aria-hidden /> Проверить подключение
          </Button>
          {a.status === "active" ? (
            <Button size="sm" variant="ghost" disabled={paused.isPending} onClick={() => paused.mutate({ id: a.id, pause: true })}>
              <Pause className="size-3.5" aria-hidden /> Пауза
            </Button>
          ) : (
            <Button size="sm" variant="ghost" disabled={paused.isPending} onClick={() => paused.mutate({ id: a.id, pause: false })}>
              <Play className="size-3.5" aria-hidden /> Возобновить
            </Button>
          )}
          {!editing ? (
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              <Pencil className="size-3.5" aria-hidden /> Изменить
            </Button>
          ) : null}
          <span className="text-xs text-zinc-500">проверено: {formatDateTime(a.last_checked_at)}</span>
        </div>
      ) : null}
      <InlineError error={verify.error ?? paused.error} />
      {editing ? (
        <div className="mt-3 border-t border-zinc-100 pt-3 dark:border-zinc-800">
          <AccountFormView account={a} onDone={() => setEditing(false)} />
        </div>
      ) : null}
    </Card>
  );
}

function AccountFormView({ account, onDone }: { account?: EmailAccount; onDone: () => void }) {
  const save = useSaveEmailAccount();
  const saved = account ? accountForm(account) : DEFAULT_ACCOUNT_FORM;
  const [form, setForm] = useState<AccountForm>(saved);
  const [errors, setErrors] = useState<string[]>([]);
  const set = <K extends keyof AccountForm>(k: K, v: AccountForm[K]) => setForm((f) => ({ ...f, [k]: v }));
  const id = (name: string) => `acc-${account?.id ?? "new"}-${name}`;
  const num = (v: string) => (v === "" ? Number.NaN : Number(v));

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        const errs = accountFormErrors(form, { editing: !!account });
        setErrors(errs);
        if (errs.length) return;
        if (account) {
          const body = accountPatchRequest(saved, form);
          if (!Object.keys(body).length) return onDone();
          save.mutate({ id: account.id, body }, { onSuccess: onDone });
        } else {
          save.mutate({ body: accountCreateRequest(form) }, { onSuccess: onDone });
        }
      }}
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <Label htmlFor={id("name")}>Название</Label>
          <Input id={id("name")} value={form.name} maxLength={200} placeholder="Основной" onChange={(e) => set("name", e.currentTarget.value)} />
        </div>
        <div>
          <Label htmlFor={id("from")}>Адрес отправителя</Label>
          <Input id={id("from")} type="email" value={form.from_email} maxLength={320} onChange={(e) => set("from_email", e.currentTarget.value)} />
        </div>
        <div>
          <Label htmlFor={id("from-name")}>Имя отправителя</Label>
          <Input id={id("from-name")} value={form.from_name} maxLength={200} onChange={(e) => set("from_name", e.currentTarget.value)} />
        </div>
        <div>
          <Label htmlFor={id("host")}>SMTP-сервер</Label>
          <Input id={id("host")} value={form.smtp_host} maxLength={255} placeholder="smtp.example.com" onChange={(e) => set("smtp_host", e.currentTarget.value)} />
        </div>
        <div>
          <Label htmlFor={id("security")}>Шифрование</Label>
          <Select
            id={id("security")}
            value={form.smtp_security}
            onChange={(e) => {
              const security = e.currentTarget.value as SmtpSecurity;
              // follow the standard port unless the user typed a custom one
              setForm((f) => ({
                ...f,
                smtp_security: security,
                smtp_port: f.smtp_port === DEFAULT_PORTS[f.smtp_security] ? DEFAULT_PORTS[security] : f.smtp_port,
              }));
            }}
          >
            {(Object.keys(SMTP_SECURITY_LABELS) as SmtpSecurity[]).map((s) => (
              <option key={s} value={s}>
                {SMTP_SECURITY_LABELS[s]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor={id("port")}>Порт</Label>
          <Input id={id("port")} type="number" min={1} max={65535} value={Number.isNaN(form.smtp_port) ? "" : form.smtp_port} onChange={(e) => set("smtp_port", num(e.currentTarget.value))} />
        </div>
        <div>
          <Label htmlFor={id("user")}>Логин</Label>
          <Input id={id("user")} value={form.smtp_username} maxLength={320} autoComplete="off" placeholder="обычно = адрес" onChange={(e) => set("smtp_username", e.currentTarget.value)} />
        </div>
        <div>
          <Label htmlFor={id("password")}>Пароль</Label>
          <Input
            id={id("password")}
            type="password"
            value={form.password}
            maxLength={500}
            autoComplete="new-password"
            placeholder={account?.password_configured ? "оставьте пустым, чтобы не менять" : ""}
            onChange={(e) => set("password", e.currentTarget.value)}
          />
        </div>
        <div>
          <Label htmlFor={id("reply-to")}>Reply-To</Label>
          <Input id={id("reply-to")} type="email" value={form.reply_to} maxLength={320} placeholder="необязательно" onChange={(e) => set("reply_to", e.currentTarget.value)} />
        </div>
        <div>
          <Label htmlFor={id("limit")}>Писем в день (до {MAX_DAILY_LIMIT})</Label>
          <Input id={id("limit")} type="number" min={1} max={MAX_DAILY_LIMIT} value={Number.isNaN(form.daily_limit) ? "" : form.daily_limit} onChange={(e) => set("daily_limit", num(e.currentTarget.value))} />
        </div>
        <div>
          <Label htmlFor={id("cd-min")}>Пауза от, с</Label>
          <Input id={id("cd-min")} type="number" min={0} max={MAX_COOLDOWN_SECONDS} value={Number.isNaN(form.cooldown_min_seconds) ? "" : form.cooldown_min_seconds} onChange={(e) => set("cooldown_min_seconds", num(e.currentTarget.value))} />
        </div>
        <div>
          <Label htmlFor={id("cd-max")}>Пауза до, с</Label>
          <Input id={id("cd-max")} type="number" min={0} max={MAX_COOLDOWN_SECONDS} value={Number.isNaN(form.cooldown_max_seconds) ? "" : form.cooldown_max_seconds} onChange={(e) => set("cooldown_max_seconds", num(e.currentTarget.value))} />
        </div>
      </div>
      <div>
        <Label htmlFor={id("opt-out")}>Текст отказа от рассылки (добавляется в конец каждого письма)</Label>
        <Textarea
          id={id("opt-out")}
          rows={2}
          maxLength={MAX_OPT_OUT}
          value={form.opt_out_text}
          placeholder={account ? "" : "по умолчанию: «Если вы не хотите получать от меня письма, просто ответьте «нет»…»"}
          className="font-sans"
          onChange={(e) => set("opt_out_text", e.currentTarget.value)}
        />
      </div>
      {errors.map((m) => (
        <p key={m} className="text-sm text-red-600">
          {m}
        </p>
      ))}
      <div className="flex items-center gap-2">
        <Button type="submit" disabled={save.isPending}>
          {account ? "Сохранить" : "Добавить"}
        </Button>
        <Button variant="ghost" onClick={onDone}>
          Отмена
        </Button>
        <span className="text-xs text-zinc-500">Пароль шифруется и больше не показывается.</span>
      </div>
      {save.error ? (
        <p role="alert" className="text-sm text-red-600">
          {offerErrorMessage(save.error) ?? errorMessage(save.error)}
        </p>
      ) : null}
    </form>
  );
}

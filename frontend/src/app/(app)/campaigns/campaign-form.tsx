"use client";

import { useState } from "react";

import { errorMessage } from "@/components/states";
import { Button, Input, Label, Select, Textarea } from "@/components/ui";
import { useSaveCampaign, type Campaign } from "@/lib/api/campaigns-hooks";
import { useProjects } from "@/lib/api/hooks";
import { useOfferTemplates } from "@/lib/api/offers-hooks";
import { useEmailAccounts } from "@/lib/api/outreach-hooks";
import {
  DEFAULT_CAMPAIGN_FORM,
  MAX_CAMPAIGN_DAILY_LIMIT,
  MAX_CAMPAIGN_INSTRUCTIONS,
  MAX_FAILURES_LIMIT,
  TIMEZONES,
  WEEKDAYS,
  campaignCreateRequest,
  campaignErrorMessage,
  campaignForm,
  campaignFormErrors,
  campaignPatchRequest,
  toggleIn,
  type CampaignForm,
} from "@/lib/campaigns";
import { CHANNELS, CHANNEL_LABELS, STYLE_LABELS, type OfferStyle } from "@/lib/offers";
import { ACCOUNT_STATUS_LABELS } from "@/lib/outreach";
import { cn } from "@/lib/utils";

const STYLES = Object.keys(STYLE_LABELS) as OfferStyle[];
const DAYS = WEEKDAYS.map((d) => d.day);

/** ТЗ §29: the campaign's settings. Saving never sends anything and never approves an offer. */
export function CampaignFormView({ campaign, onDone }: { campaign?: Campaign; onDone: (saved?: Campaign) => void }) {
  const save = useSaveCampaign();
  const projects = useProjects({ offset: 0 });
  const templates = useOfferTemplates();
  const accounts = useEmailAccounts();
  const saved = campaign ? campaignForm(campaign) : DEFAULT_CAMPAIGN_FORM;
  const [form, setForm] = useState<CampaignForm>(saved);
  const [errors, setErrors] = useState<string[]>([]);
  const set = <K extends keyof CampaignForm>(k: K, v: CampaignForm[K]) => setForm((f) => ({ ...f, [k]: v }));
  const id = (name: string) => `campaign-${campaign?.id ?? "new"}-${name}`;
  const num = (v: string) => (v === "" ? Number.NaN : Number(v));
  const accountIds = (accounts.data ?? []).map((a) => a.id);
  // a template archived since it was chosen still shows (the backend refuses to write with it)
  const templateOptions = (templates.data ?? []).map((t) => ({ id: t.id, name: t.name }));
  if (campaign?.template_id && !templateOptions.some((t) => t.id === campaign.template_id)) {
    templateOptions.push({ id: campaign.template_id, name: `${campaign.template_name ?? "шаблон"} (в архиве)` });
  }

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        const errs = campaignFormErrors(form);
        setErrors(errs);
        if (errs.length) return;
        if (campaign) {
          const body = campaignPatchRequest(saved, form);
          if (!Object.keys(body).length) return onDone(campaign);
          save.mutate({ id: campaign.id, body }, { onSuccess: (c) => onDone(c) });
        } else {
          save.mutate({ body: campaignCreateRequest(form) }, { onSuccess: (c) => onDone(c) });
        }
      }}
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="sm:col-span-2">
          <Label htmlFor={id("name")}>Название</Label>
          <Input id={id("name")} value={form.name} maxLength={200} placeholder="Бизнес-каналы, ноябрь" onChange={(e) => set("name", e.currentTarget.value)} />
        </div>
        <div>
          <Label htmlFor={id("project")}>Проект</Label>
          <Select
            id={id("project")}
            value={form.project_id ?? ""}
            onChange={(e) => set("project_id", e.currentTarget.value ? Number(e.currentTarget.value) : null)}
          >
            <option value="">без проекта</option>
            {projects.data?.items.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <div>
        <Label htmlFor={id("description")}>Описание</Label>
        <Input id={id("description")} value={form.description} maxLength={2000} placeholder="необязательно" onChange={(e) => set("description", e.currentTarget.value)} />
      </div>

      <fieldset className="space-y-2 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
        <legend className="px-1 text-xs font-semibold text-zinc-600 dark:text-zinc-400">Оффер</legend>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="sm:col-span-2">
            <Label htmlFor={id("template")}>Шаблон оффера</Label>
            <Select
              id={id("template")}
              value={form.template_id ?? ""}
              onChange={(e) => set("template_id", e.currentTarget.value ? Number(e.currentTarget.value) : null)}
            >
              <option value="">без шаблона</option>
              {templateOptions.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor={id("language")}>Язык</Label>
            <Input id={id("language")} value={form.language} maxLength={10} placeholder="как у канала" onChange={(e) => set("language", e.currentTarget.value)} />
          </div>
        </div>
        <div>
          <Label htmlFor={id("instructions")}>Инструкции для черновиков</Label>
          <Textarea
            id={id("instructions")}
            rows={2}
            maxLength={MAX_CAMPAIGN_INSTRUCTIONS}
            value={form.instructions}
            className="font-sans"
            placeholder="необязательно: что подчеркнуть, чего избегать"
            onChange={(e) => set("instructions", e.currentTarget.value)}
          />
        </div>
        <div role="group" aria-labelledby={id("styles-label")}>
          <p id={id("styles-label")} className="mb-1 text-xs font-medium text-zinc-600 dark:text-zinc-400">
            Варианты оффера для A/B (каждый лид получает один, поровну)
          </p>
          <div className="flex flex-wrap gap-3 text-sm">
            {STYLES.map((s) => (
              <label key={s} className="inline-flex items-center gap-1.5">
                <input type="checkbox" className="size-4" checked={form.styles.includes(s)} onChange={() => set("styles", toggleIn(form.styles, s, STYLES))} />
                {STYLE_LABELS[s]}
              </label>
            ))}
          </div>
        </div>
      </fieldset>

      <fieldset className="space-y-2 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
        <legend className="px-1 text-xs font-semibold text-zinc-600 dark:text-zinc-400">Отправка</legend>
        <div role="group" aria-labelledby={id("channels-label")}>
          <p id={id("channels-label")} className="mb-1 text-xs font-medium text-zinc-600 dark:text-zinc-400">
            Каналы (в порядке предпочтения; Telegram, VK и «вручную» вы отправляете сами)
          </p>
          <div className="flex flex-wrap gap-3 text-sm">
            {CHANNELS.map((c) => (
              <label key={c} className="inline-flex items-center gap-1.5">
                <input type="checkbox" className="size-4" checked={form.channels.includes(c)} onChange={() => set("channels", toggleIn(form.channels, c, CHANNELS))} />
                {CHANNEL_LABELS[c]}
              </label>
            ))}
          </div>
        </div>
        {form.channels.includes("email") ? (
          <div role="group" aria-labelledby={id("accounts-label")}>
            <p id={id("accounts-label")} className="mb-1 text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Почтовые аккаунты (ничего не выбрано — любой активный)
            </p>
            {accounts.data && accounts.data.length === 0 ? (
              <p className="text-sm text-amber-700 dark:text-amber-400">Почтовых аккаунтов нет — добавьте в разделе «Почтовые аккаунты».</p>
            ) : (
              <div className="flex flex-wrap gap-3 text-sm">
                {(accounts.data ?? []).map((a) => (
                  <label key={a.id} className="inline-flex items-center gap-1.5">
                    <input
                      type="checkbox"
                      className="size-4"
                      checked={form.email_account_ids.includes(a.id)}
                      onChange={() => set("email_account_ids", toggleIn(form.email_account_ids, a.id, accountIds))}
                    />
                    {a.name} <span className="text-zinc-500">{a.from_email}</span>
                    {a.status !== "active" ? <span className="text-xs text-amber-700 dark:text-amber-400">({ACCOUNT_STATUS_LABELS[a.status]})</span> : null}
                  </label>
                ))}
              </div>
            )}
          </div>
        ) : null}
        <div role="group" aria-labelledby={id("days-label")}>
          <p id={id("days-label")} className="mb-1 text-xs font-medium text-zinc-600 dark:text-zinc-400">
            Дни отправки
          </p>
          <div className="flex flex-wrap gap-1">
            {WEEKDAYS.map(({ day, label }) => {
              const on = form.send_days.includes(day);
              return (
                <button
                  key={day}
                  type="button"
                  aria-pressed={on}
                  onClick={() => set("send_days", toggleIn(form.send_days, day, DAYS))}
                  className={cn(
                    "h-7 w-9 rounded-md border text-xs",
                    on
                      ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
                      : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800",
                  )}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <div>
            <Label htmlFor={id("from")}>Окно: с</Label>
            <Input id={id("from")} type="time" value={form.send_from} onChange={(e) => set("send_from", e.currentTarget.value)} />
          </div>
          <div>
            <Label htmlFor={id("to")}>Окно: до</Label>
            <Input id={id("to")} type="time" value={form.send_to} onChange={(e) => set("send_to", e.currentTarget.value)} />
          </div>
          <div>
            <Label htmlFor={id("tz")}>Часовой пояс</Label>
            <Input id={id("tz")} list={id("tz-list")} value={form.timezone} maxLength={64} onChange={(e) => set("timezone", e.currentTarget.value)} />
            <datalist id={id("tz-list")}>
              {TIMEZONES.map((z) => (
                <option key={z} value={z} />
              ))}
            </datalist>
          </div>
          <div>
            <Label htmlFor={id("starts")}>Не раньше (необязательно)</Label>
            <Input id={id("starts")} type="datetime-local" value={form.starts_at} onChange={(e) => set("starts_at", e.currentTarget.value)} />
          </div>
          <div>
            <Label htmlFor={id("limit")}>Писем в день (до {MAX_CAMPAIGN_DAILY_LIMIT})</Label>
            <Input
              id={id("limit")}
              type="number"
              min={1}
              max={MAX_CAMPAIGN_DAILY_LIMIT}
              value={Number.isNaN(form.daily_limit) ? "" : form.daily_limit}
              onChange={(e) => set("daily_limit", num(e.currentTarget.value))}
            />
          </div>
          <div>
            <Label htmlFor={id("failures")}>Автопауза после ошибок подряд</Label>
            <Input
              id={id("failures")}
              type="number"
              min={1}
              max={MAX_FAILURES_LIMIT}
              value={Number.isNaN(form.max_consecutive_failures) ? "" : form.max_consecutive_failures}
              onChange={(e) => set("max_consecutive_failures", num(e.currentTarget.value))}
            />
          </div>
        </div>
      </fieldset>

      {errors.map((m) => (
        <p key={m} className="text-sm text-red-600">
          {m}
        </p>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={save.isPending}>
          {campaign ? "Сохранить" : "Создать кампанию"}
        </Button>
        <Button variant="ghost" onClick={() => onDone()}>
          Отмена
        </Button>
        <span className="text-xs text-zinc-500">
          Лимиты почтового аккаунта тоже действуют. Каждый оффер кампании всё равно подтверждается вручную.
        </span>
      </div>
      {save.error ? (
        <p role="alert" className="text-sm text-red-600">
          {campaignErrorMessage(save.error) ?? errorMessage(save.error)}
        </p>
      ) : null}
    </form>
  );
}

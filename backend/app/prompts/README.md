# Prompt templates

`{name}/v{N}.md` — one file per version (ADR-0007 §5, `app/domains/ai/prompts.py`).

```
System prompt text … $variable …
=== user ===
User message text … ${variable} …
```

- A version is registered in `prompt_templates` (SHA-256) on first use. **Never edit a used version** —
  the runner refuses a file whose hash differs from the registered one; add `v{N+1}.md` instead.
- Placeholders: `string.Template` (`$name`, `${name}`); a missing variable is an error. A literal `$` is `$$`.
- Do not ask the model for numbers the code computes (overall score, lead score) or for CTR promises
  (AI_ARCHITECTURE §2).

Prompts of the analysis tasks are added with them (Phase 3.5+: `thumbnail_analysis/v1.md`, …).

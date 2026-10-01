"""Versioned prompt templates (ADR-0007 §5, AI_ARCHITECTURE "Prompt versioning").

File layout: ``app/prompts/{name}/v{N}.md``. The part before the line ``=== user ===`` is the system
prompt, the rest the user message. Placeholders use ``string.Template`` syntax (``$title`` / ``${title}``);
a missing variable is an error, never an empty string. (ADR-0007 named Jinja2 — not needed for plain
substitution, so no extra dependency.)

On first use the file is registered in ``prompt_templates`` with its SHA-256. If the file later differs
from the registered hash the call is refused: results must stay attributable to the exact prompt text,
so a changed prompt needs a new version file.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.domains.ai.models import PromptTemplate

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
USER_MARKER = "=== user ==="
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


class PromptError(AppError):
    code = "prompt_error"


@dataclass(frozen=True)
class LoadedPrompt:
    template_id: int
    name: str
    version: int
    system: str
    user: str

    def render(self, variables: dict[str, Any]) -> tuple[str, str]:
        try:
            return (
                Template(self.system).substitute(variables).strip(),
                Template(self.user).substitute(variables).strip(),
            )
        except (KeyError, ValueError) as exc:
            raise PromptError(f"Prompt {self.name}/v{self.version}: missing or bad variable {exc}") from exc


def prompt_path(name: str, version: int, root: Path = PROMPTS_DIR) -> Path:
    if not _NAME.match(name) or version < 1:
        raise PromptError(f"Invalid prompt reference {name!r} v{version}")
    return root / name / f"v{version}.md"


def split_prompt(body: str) -> tuple[str, str]:
    if USER_MARKER not in body:
        raise PromptError(f"Prompt file has no '{USER_MARKER}' separator line")
    system, user = body.split(USER_MARKER, 1)
    return system.strip(), user.strip()


async def load_prompt(
    db: AsyncSession, name: str, version: int, output_schema: dict[str, Any], *, root: Path = PROMPTS_DIR
) -> LoadedPrompt:
    path = prompt_path(name, version, root)
    try:
        body = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except FileNotFoundError as exc:
        raise PromptError(f"Prompt file {name}/v{version}.md not found") from exc
    system, user = split_prompt(body)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()

    await db.execute(
        insert(PromptTemplate)
        .values(name=name, version=version, content_hash=digest, body=body, output_schema=output_schema)
        .on_conflict_do_nothing(index_elements=["name", "version"])
    )
    row = await db.scalar(
        select(PromptTemplate).where(PromptTemplate.name == name, PromptTemplate.version == version)
    )
    assert row is not None
    if row.content_hash != digest:
        raise PromptError(
            f"Prompt {name}/v{version}.md was modified after it had been used. Keep v{version} unchanged "
            f"and add v{version + 1}.md instead.",
            code="prompt_changed",
        )
    return LoadedPrompt(template_id=row.id, name=name, version=version, system=system, user=user)

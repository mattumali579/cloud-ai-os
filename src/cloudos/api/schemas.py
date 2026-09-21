"""Pydantic request models for the Agent API (§7)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=200)
    payload: dict = Field(default_factory=dict)
    priority: int = Field(default=100, ge=0, le=10_000)
    run_at: Optional[datetime] = None
    max_attempts: int = Field(default=3, ge=1, le=100)


class InvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    task: str = Field(default="general", min_length=1, max_length=200)
    privacy_label: str = Field(default="internal")
    model_hint: Optional[str] = None
    max_tokens: int = Field(default=1024, ge=1, le=65_536)


class EmployeeInvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=20_000)
    history: list[str] = Field(default_factory=list, max_length=12)
    privacy_label: str = Field(default="internal")
    max_tokens: int = Field(default=4096, ge=1, le=65_536)


class HiggsfieldGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern="^(image|video)$")
    prompt: str = Field(min_length=1, max_length=8_000)
    confirmed: bool = False


class EmailDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(min_length=1, max_length=64)


class EmailBuildRequest(EmailDraftRequest):
    """Turn an email-marketer reply into a reviewable draft on disk."""

    source_text: str = Field(min_length=1, max_length=200_000)


class EmailSendRequest(EmailDraftRequest):
    fingerprint: str = Field(pattern="^[a-fA-F0-9]{12}$")

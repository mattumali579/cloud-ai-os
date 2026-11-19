"""Pydantic request models for the Agent API (§7)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

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


class GlassesVisionRequest(BaseModel):
    """One authorized glasses-vision turn. Images are optional stills, not a stream."""

    model_config = ConfigDict(extra="forbid")

    utterance: str = Field(min_length=1, max_length=2_000)
    image_base64: Optional[str] = Field(default=None, max_length=20_000_000)
    mime: str = Field(default="image/x-portable-pixmap", max_length=80)
    consent: bool = False
    face_crop: bool = False
    thermal_c: Optional[float] = Field(default=None, ge=-20, le=120)
    thermal_state: Optional[str] = Field(default=None, max_length=32)
    battery_percent: Optional[int] = Field(default=None, ge=0, le=100)
    retain_frames: bool = False
    device_token: Optional[str] = Field(default=None, max_length=200)
    request_id: Optional[str] = Field(default=None, max_length=80)


class PhoneTaskRequest(BaseModel):
    """Small, DB-independent task request accepted from the private phone UI."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=12_000)
    agent: Literal["auto", "claude", "codex"] = "auto"


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


class RevenueOSRunRequest(BaseModel):
    """Whitelisted visual-controller action; no arbitrary command execution."""
    model_config = ConfigDict(extra="forbid")
    action: Literal["state", "decide", "research", "plan", "build", "troubleshoot", "notify", "product_verify", "lead_status", "lead_cycle", "outreach_qa", "reply_check", "send"]
    execution_key: Optional[str] = Field(default=None, max_length=120)
    goal: Optional[str] = Field(default=None, min_length=1, max_length=2_000)
    allow_send: bool = False

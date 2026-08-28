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

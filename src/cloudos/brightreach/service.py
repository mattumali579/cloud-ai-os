"""The smallest durable BrightReach lead-to-booking flow.

This module deliberately does not send email/texts or create calendar events.
Those actions require a client's connected account.  Instead it creates the
next approved action, records every state change in a local JSONL evidence
ledger, and accepts a booking only after an outside calendar connector reports
its real event identifier.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cloudos.config import REPO_ROOT


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


class QualificationRule(BaseModel):
    """One answer a client requires before a person may book."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=240)
    accepted_values: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("field", "question")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _clean(value)

    @field_validator("accepted_values")
    @classmethod
    def clean_values(cls, values: list[str]) -> list[str]:
        return [_clean(value) for value in values if _clean(value)]


class ClientSetup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    service: str = Field(min_length=1, max_length=160)
    location: str = Field(min_length=1, max_length=160)
    ideal_customer: str = Field(default="", max_length=500)
    disqualifiers: list[str] = Field(default_factory=list, max_length=20)
    contact_information: dict[str, str] = Field(default_factory=dict)
    offer: str = Field(default="", max_length=500)
    customer_value: str = Field(default="", max_length=160)
    lead_source: list[str] = Field(default_factory=list, max_length=20)
    qualification_rules: list[QualificationRule] = Field(default_factory=list, max_length=12)
    appointment_minutes: int = Field(default=30, ge=5, le=240)
    calendar_name: str = Field(default="", max_length=160)

    @field_validator("name", "service", "location", "ideal_customer", "offer", "customer_value", "calendar_name")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return _clean(value)

    @field_validator("disqualifiers", "lead_source")
    @classmethod
    def clean_lists(cls, values: list[str]) -> list[str]:
        return [_clean(value) for value in values if _clean(value)]

    @field_validator("contact_information")
    @classmethod
    def clean_contact(cls, values: dict[str, str]) -> dict[str, str]:
        return {_clean(str(key)): _clean(str(value)) for key, value in values.items() if _clean(str(key))}


class LeadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=64)
    campaign: str = Field(default="", max_length=160)
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=48)
    location: str = Field(default="", max_length=160)
    service_requested: str = Field(default="", max_length=160)
    timestamp: datetime | None = None
    answers: dict[str, str] = Field(default_factory=dict)
    opted_out: bool = False

    @field_validator("source", "campaign", "name", "email", "phone", "location", "service_requested")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return _clean(value)

    @field_validator("timestamp")
    @classmethod
    def require_timestamp_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp must include a timezone")
        return value

    @field_validator("answers")
    @classmethod
    def clean_answers(cls, answers: dict[str, str]) -> dict[str, str]:
        return {_clean(str(key)): _clean(str(value)) for key, value in answers.items() if _clean(str(key))}


class BookingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar_event_id: str = Field(min_length=1, max_length=240)
    scheduled_at: datetime
    calendar_name: str = Field(min_length=1, max_length=160)

    @field_validator("calendar_event_id", "calendar_name")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return _clean(value)

    @field_validator("scheduled_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must include a timezone")
        return value


class Ledger:
    """Append-only local evidence. Runtime data is ignored by Git."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        event = {"event_id": str(uuid.uuid4()), "recorded_at": _now(), **event}
        line = json.dumps(event, sort_keys=True, separators=(",", ":"))
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return event

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        events: list[dict[str, Any]] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                events.append(item)
        return events


class BrightReachService:
    """Rules-first orchestration for a single service client and its leads."""

    def __init__(self, ledger_path: Path | None = None) -> None:
        self.ledger = Ledger(ledger_path or REPO_ROOT / "data" / "brightreach" / "evidence.jsonl")

    def _events(self, kind: str | None = None) -> list[dict[str, Any]]:
        events = self.ledger.events()
        return [event for event in events if event.get("kind") == kind] if kind else events

    def _client(self, client_id: str) -> dict[str, Any]:
        for event in reversed(self._events("client.created")):
            if event.get("client_id") == client_id:
                return dict(event["client"])
        raise KeyError("client not found")

    def _lead(self, lead_id: str) -> dict[str, Any]:
        for event in reversed(self._events("lead.created")):
            if event.get("lead_id") == lead_id:
                return dict(event["lead"])
        raise KeyError("lead not found")

    def _lead_events(self, lead_id: str) -> list[dict[str, Any]]:
        return [event for event in self._events() if event.get("lead_id") == lead_id]

    def _status(self, lead_id: str) -> str:
        events = self._lead_events(lead_id)
        if not events:
            raise KeyError("lead not found")
        return str(events[-1].get("status", "new"))

    def create_client(self, setup: ClientSetup) -> dict[str, Any]:
        client_id = str(uuid.uuid4())
        client = setup.model_dump(mode="json")
        self.ledger.append({"kind": "client.created", "client_id": client_id, "client": client, "status": "active"})
        return {"client_id": client_id, "client": client}

    def create_lead(self, client_id: str, input_: LeadInput) -> dict[str, Any]:
        client = self._client(client_id)
        lead_id = str(uuid.uuid4())
        lead = input_.model_dump(mode="json")
        lead["timestamp"] = lead["timestamp"] or _now()
        status = "opted_out" if lead["opted_out"] else "responded"
        response = self._instant_response(client, lead) if status == "responded" else None
        self.ledger.append(
            {
                "kind": "lead.created",
                "client_id": client_id,
                "lead_id": lead_id,
                "lead": lead,
                "status": status,
                "source": lead["source"],
            }
        )
        if response:
            self.ledger.append(
                {
                    "kind": "response.ready",
                    "client_id": client_id,
                    "lead_id": lead_id,
                    "status": "responded",
                    "delivery": "pending_connected_channel",
                    "message": response,
                }
            )
        return {"lead_id": lead_id, "status": status, "response": response}

    @staticmethod
    def _instant_response(client: dict[str, Any], lead: dict[str, Any]) -> str:
        contact = client.get("contact_information", {})
        contact_line = " ".join(f"{key}: {value}" for key, value in contact.items())
        offer = client.get("offer") or f"help with {client['service']}"
        return f"Hi {lead['name']}, thanks for contacting {client['name']}. {offer}. {contact_line}".strip()

    def qualify(self, lead_id: str) -> dict[str, Any]:
        lead = self._lead(lead_id)
        events = self._lead_events(lead_id)
        client_id = str(events[0]["client_id"])
        client = self._client(client_id)
        current = self._status(lead_id)
        if current in {"opted_out", "booked", "declined", "NOT_QUALIFIED"}:
            return {"lead_id": lead_id, "status": current, "qualified": False, "reason": "lead is closed"}

        reasons: list[str] = []
        disqualifier_hits: list[str] = []
        haystack = " ".join(
            [lead.get("name", ""), lead.get("location", ""), lead.get("service_requested", ""), *lead["answers"].values()]
        ).casefold()
        for term in client.get("disqualifiers", []):
            if term.casefold() in haystack:
                disqualifier_hits.append(term)
        if disqualifier_hits:
            reasons.extend(f"disqualifier: {term}" for term in disqualifier_hits)
        if not lead["location"] or client["location"].casefold() not in lead["location"].casefold():
            reasons.append("outside service area")
        requested = lead.get("service_requested", "")
        if requested and requested.casefold() != client["service"].casefold():
            reasons.append("service requested does not match configured service")
        answers = {key.casefold(): value for key, value in lead["answers"].items()}
        missing = False
        for rule in client["qualification_rules"]:
            answer = answers.get(rule["field"].casefold(), "")
            if not answer:
                reasons.append(f"missing {rule['field']}")
                missing = True
            elif rule["accepted_values"] and answer.casefold() not in {
                value.casefold() for value in rule["accepted_values"]
            }:
                reasons.append(f"{rule['field']} does not meet rule")

        qualified = not reasons
        if qualified:
            status = "QUALIFIED"
        elif disqualifier_hits or "outside service area" in reasons or "service requested does not match configured service" in reasons:
            status = "NOT_QUALIFIED"
        elif missing:
            status = "NEEDS_HUMAN_REVIEW"
        else:
            status = "NOT_QUALIFIED"
        next_action = (
            "offer_booking"
            if qualified
            else "human_review" if status == "NEEDS_HUMAN_REVIEW" else "stop_follow_up"
        )
        self.ledger.append(
            {
                "kind": "lead.qualified",
                "client_id": client_id,
                "lead_id": lead_id,
                "status": status,
                "qualified": qualified,
                "reasons": reasons,
                "next_action": next_action,
            }
        )
        return {"lead_id": lead_id, "status": status, "qualified": qualified, "reasons": reasons, "next_action": next_action}

    def follow_up(self, lead_id: str) -> dict[str, Any]:
        lead = self._lead(lead_id)
        current = self._status(lead_id)
        events = self._lead_events(lead_id)
        client_id = str(events[0]["client_id"])
        if current in {"opted_out", "booked", "declined", "NOT_QUALIFIED"}:
            return {"lead_id": lead_id, "status": current, "sent": False, "reason": "lead is closed"}
        message = f"Hi {lead['name']}, checking whether you still need help. Reply when you are ready and we will confirm the next step."
        self.ledger.append(
            {
                "kind": "follow_up.ready",
                "client_id": client_id,
                "lead_id": lead_id,
                "status": "NEEDS_HUMAN_REVIEW",
                "delivery": "pending_connected_channel",
                "message": message,
            }
        )
        return {"lead_id": lead_id, "status": "NEEDS_HUMAN_REVIEW", "sent": False, "message": message}

    def record_booking(self, lead_id: str, booking: BookingInput) -> dict[str, Any]:
        lead = self._lead(lead_id)
        events = self._lead_events(lead_id)
        client_id = str(events[0]["client_id"])
        if self._status(lead_id) != "QUALIFIED":
            raise ValueError("only qualified leads can be booked")
        client = self._client(client_id)
        if client.get("calendar_name") and booking.calendar_name != client["calendar_name"]:
            raise ValueError("booking calendar does not match client calendar")
        proof = booking.model_dump(mode="json")
        self.ledger.append(
            {
                "kind": "booking.confirmed",
                "client_id": client_id,
                "lead_id": lead_id,
                "status": "booked",
                "lead_name": lead["name"],
                "proof": proof,
            }
        )
        return {"lead_id": lead_id, "status": "booked", "proof": proof}

    def report(self, client_id: str) -> dict[str, Any]:
        self._client(client_id)
        leads = [event for event in self._events("lead.created") if event.get("client_id") == client_id]
        statuses: Counter[str] = Counter()
        sources: Counter[str] = Counter()
        campaigns: Counter[str] = Counter()
        bookings = 0
        for lead_event in leads:
            lead_id = str(lead_event["lead_id"])
            statuses[self._status(lead_id)] += 1
            sources[str(lead_event.get("source", "unknown"))] += 1
            campaigns[str(lead_event.get("lead", {}).get("campaign", "")) or "unknown"] += 1
            bookings += int(self._status(lead_id) == "booked")
        return {
            "client_id": client_id,
            "leads": len(leads),
            "bookings": bookings,
            "by_status": dict(sorted(statuses.items())),
            "by_source": dict(sorted(sources.items())),
            "by_campaign": dict(sorted(campaigns.items())),
            "evidence_events": len([event for event in self._events() if event.get("client_id") == client_id]),
        }


@lru_cache(maxsize=1)
def get_service() -> BrightReachService:
    return BrightReachService()

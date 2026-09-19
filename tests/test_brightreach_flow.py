from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cloudos.brightreach.service import BookingInput, BrightReachService, ClientSetup, LeadInput, QualificationRule


def service(tmp_path) -> BrightReachService:
    return BrightReachService(tmp_path / "evidence.jsonl")


def client_setup() -> ClientSetup:
    return ClientSetup(
        name="Northside Roof Care",
        service="roof inspection",
        location="Austin",
        qualification_rules=[
            QualificationRule(field="timeline", question="When do you need help?", accepted_values=["now", "this week"]),
            QualificationRule(field="decision_maker", question="Can you approve this work?", accepted_values=["yes"]),
        ],
        calendar_name="Northside calendar",
    )


def test_full_lead_to_booking_flow_persists_proof(tmp_path):
    flow = service(tmp_path)
    client = flow.create_client(client_setup())
    lead = flow.create_lead(
        client["client_id"],
        LeadInput(
            source="landing_page",
            name="Jamie",
            email="jamie@example.com",
            location="Austin, TX",
            answers={"timeline": "this week", "decision_maker": "yes"},
        ),
    )
    assert lead["status"] == "responded"
    assert "thanks for contacting" in lead["response"].lower()

    qualification = flow.qualify(lead["lead_id"])
    assert qualification["qualified"] is True
    assert qualification["next_action"] == "offer_booking"

    booked = flow.record_booking(
        lead["lead_id"],
        BookingInput(
            calendar_event_id="google-event-123",
            scheduled_at=datetime(2026, 9, 20, 15, 0, tzinfo=UTC),
            calendar_name="Northside calendar",
        ),
    )
    assert booked["status"] == "booked"
    assert booked["proof"]["calendar_event_id"] == "google-event-123"

    report = flow.report(client["client_id"])
    assert report["leads"] == 1
    assert report["bookings"] == 1
    assert report["by_source"] == {"landing_page": 1}
    assert report["evidence_events"] == 5

    reloaded = service(tmp_path)
    assert reloaded.report(client["client_id"])["bookings"] == 1


def test_out_of_area_lead_is_not_allowed_to_book(tmp_path):
    flow = service(tmp_path)
    client = flow.create_client(client_setup())
    lead = flow.create_lead(
        client["client_id"],
        LeadInput(source="ads", name="Morgan", location="Dallas", answers={"timeline": "now", "decision_maker": "yes"}),
    )
    result = flow.qualify(lead["lead_id"])
    assert result["qualified"] is False
    assert "outside service area" in result["reasons"]
    with pytest.raises(ValueError, match="only qualified"):
        flow.record_booking(
            lead["lead_id"],
            BookingInput(calendar_event_id="nope", scheduled_at=datetime.now(UTC), calendar_name="Northside calendar"),
        )


def test_opted_out_lead_never_gets_a_response_or_follow_up(tmp_path):
    flow = service(tmp_path)
    client = flow.create_client(client_setup())
    lead = flow.create_lead(
        client["client_id"],
        LeadInput(source="outbound", name="Taylor", location="Austin", opted_out=True),
    )
    assert lead["status"] == "opted_out"
    assert lead["response"] is None
    follow_up = flow.follow_up(lead["lead_id"])
    assert follow_up["sent"] is False
    assert follow_up["reason"] == "lead is closed"


def test_explicit_disqualifier_and_human_review_statuses(tmp_path):
    flow = service(tmp_path)
    client = flow.create_client(
        ClientSetup(
            name="Northside Roof Care",
            service="roof inspection",
            location="Austin",
            disqualifiers=["insurance claim"],
            qualification_rules=[QualificationRule(field="timeline", question="When?", accepted_values=["now"])],
        )
    )
    disqualified = flow.create_lead(
        client["client_id"],
        LeadInput(source="ads", name="Casey", location="Austin", answers={"timeline": "now", "reason": "insurance claim"}),
    )
    assert flow.qualify(disqualified["lead_id"])["status"] == "NOT_QUALIFIED"
    review = flow.create_lead(client["client_id"], LeadInput(source="ads", name="Riley", location="Austin"))
    assert flow.qualify(review["lead_id"])["status"] == "NEEDS_HUMAN_REVIEW"


def test_lead_keeps_campaign_service_and_timestamp(tmp_path):
    flow = service(tmp_path)
    client = flow.create_client(client_setup())
    lead = flow.create_lead(
        client["client_id"],
        LeadInput(source="ads", campaign="spring", name="Sam", location="Austin", service_requested="roof inspection"),
    )
    stored = flow._lead(lead["lead_id"])
    assert stored["campaign"] == "spring"
    assert stored["service_requested"] == "roof inspection"
    assert stored["timestamp"]

from __future__ import annotations

pytest_plugins = ("test_api",)

from cloudos.brightreach.service import BrightReachService, ClientSetup, LeadInput

from test_api import AUTH


def test_brightreach_api_full_flow(client, monkeypatch, tmp_path):
    from cloudos.api import app as api_app

    service = BrightReachService(tmp_path / "evidence.jsonl")
    monkeypatch.setattr(api_app, "get_service", lambda: service)

    client_response = client.post(
        "/v1/brightreach/clients",
        headers=AUTH,
        json={
            "name": "Northside Roof Care",
            "service": "roof inspection",
            "location": "Austin",
            "calendar_name": "Northside calendar",
            "qualification_rules": [
                {
                    "field": "timeline",
                    "question": "When do you need help?",
                    "accepted_values": ["now"],
                }
            ],
        },
    )
    assert client_response.status_code == 201
    client_id = client_response.json()["client_id"]

    lead_response = client.post(
        f"/v1/brightreach/clients/{client_id}/leads",
        headers=AUTH,
        json={
            "source": "ads",
            "name": "Jamie",
            "location": "Austin, TX",
            "answers": {"timeline": "now"},
        },
    )
    assert lead_response.status_code == 201
    lead_id = lead_response.json()["lead_id"]
    assert lead_response.json()["response"]

    qualified = client.post(f"/v1/brightreach/leads/{lead_id}/qualify", headers=AUTH)
    assert qualified.status_code == 200
    assert qualified.json()["qualified"] is True

    booking = client.post(
        f"/v1/brightreach/leads/{lead_id}/booking",
        headers=AUTH,
        json={
            "calendar_event_id": "event-123",
            "scheduled_at": "2026-09-20T15:00:00Z",
            "calendar_name": "Northside calendar",
        },
    )
    assert booking.status_code == 200
    assert booking.json()["proof"]["calendar_event_id"] == "event-123"

    report = client.get(f"/v1/brightreach/clients/{client_id}/report", headers=AUTH)
    assert report.status_code == 200
    assert report.json()["bookings"] == 1


def test_brightreach_booking_rejects_unqualified_lead(client, monkeypatch, tmp_path):
    from cloudos.api import app as api_app

    service = BrightReachService(tmp_path / "evidence.jsonl")
    monkeypatch.setattr(api_app, "get_service", lambda: service)
    client_id = client.post(
        "/v1/brightreach/clients",
        headers=AUTH,
        json={"name": "Northside", "service": "roof repair", "location": "Austin"},
    ).json()["client_id"]
    lead_id = client.post(
        f"/v1/brightreach/clients/{client_id}/leads",
        headers=AUTH,
        json={"source": "landing_page", "name": "Morgan", "location": "Dallas"},
    ).json()["lead_id"]
    client.post(f"/v1/brightreach/leads/{lead_id}/qualify", headers=AUTH)
    booking = client.post(
        f"/v1/brightreach/leads/{lead_id}/booking",
        headers=AUTH,
        json={
            "calendar_event_id": "event-nope",
            "scheduled_at": "2026-09-20T15:00:00Z",
            "calendar_name": "Northside calendar",
        },
    )
    assert booking.status_code == 400
    assert booking.json()["error"]["code"] == "VALIDATION_ERROR"


def test_brightreach_routes_require_authentication(client):
    response = client.post(
        "/v1/brightreach/clients",
        json={"name": "Northside", "service": "roof repair", "location": "Austin"},
    )
    assert response.status_code == 401


def test_brightreach_requires_a_timezone_on_booking(client, monkeypatch, tmp_path):
    from cloudos.api import app as api_app

    service = BrightReachService(tmp_path / "evidence.jsonl")
    monkeypatch.setattr(api_app, "get_service", lambda: service)
    client_id = service.create_client(ClientSetup(name="Northside", service="roof repair", location="Austin"))["client_id"]
    lead_id = service.create_lead(
        client_id, LeadInput(source="ads", name="Jamie", location="Austin")
    )["lead_id"]
    service.qualify(lead_id)
    response = client.post(
        f"/v1/brightreach/leads/{lead_id}/booking",
        headers=AUTH,
        json={
            "calendar_event_id": "event-123",
            "scheduled_at": "2026-09-20T15:00:00",
            "calendar_name": "Northside calendar",
        },
    )
    assert response.status_code == 400

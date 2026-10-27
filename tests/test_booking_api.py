from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE_PATH", str(tmp_path / "test.db"))
    main.init_db()
    with TestClient(main.app) as test_client:
        yield test_client


def slot_payload(start_offset_hours=1):
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=start_offset_hours)
    end = start + timedelta(minutes=30)
    return {
        "clinic_name": "Central Clinic",
        "starts_at": start.isoformat(),
        "ends_at": end.isoformat(),
    }


def create_slot(client):
    response = client.post("/slots", json=slot_payload())
    assert response.status_code == 201
    return response.json()


def booking_payload(slot_id, email="patient@example.com"):
    return {
        "slot_id": slot_id,
        "patient_name": "Test Patient",
        "patient_email": email,
    }


def test_create_and_list_slot(client):
    slot = create_slot(client)
    response = client.get("/slots")
    assert response.status_code == 200
    assert any(item["id"] == slot["id"] and item["available"] == 1 for item in response.json())


def test_slot_rejects_end_before_start(client):
    payload = slot_payload()
    payload["ends_at"] = (datetime.fromisoformat(payload["starts_at"]) - timedelta(minutes=1)).isoformat()
    response = client.post("/slots", json=payload)
    assert response.status_code == 422


def test_only_one_booking_can_claim_a_slot(client):
    slot = create_slot(client)
    first = client.post("/bookings", json=booking_payload(slot["id"]))
    second = client.post("/bookings", json=booking_payload(slot["id"], "other@example.com"))
    assert first.status_code == 201
    assert second.status_code == 409


def test_cancelled_booking_releases_slot(client):
    slot = create_slot(client)
    first = client.post("/bookings", json=booking_payload(slot["id"]))
    cancelled = client.delete(f"/bookings/{first.json()['id']}")
    second = client.post("/bookings", json=booking_payload(slot["id"], "next@example.com"))
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert second.status_code == 201


def test_cannot_delete_slot_with_active_booking(client):
    slot = create_slot(client)
    client.post("/bookings", json=booking_payload(slot["id"]))
    response = client.delete(f"/slots/{slot['id']}")
    assert response.status_code == 409


def test_missing_slot_returns_404(client):
    response = client.post("/bookings", json=booking_payload(9999))
    assert response.status_code == 404


def test_invalid_email_returns_helpful_validation_error(client):
    slot = create_slot(client)
    response = client.post("/bookings", json=booking_payload(slot["id"], "not-an-email"))
    assert response.status_code == 422

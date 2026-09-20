"""Recording storage and the quality-review process (MVP sections 29, 32, 38)."""

import io

import pytest

from backend.calls.models import CallAttempt, Disposition
from backend.calls.recordings import RecordingError, storage_key, store_recording
from backend.compliance.models import AuditLog
from backend.core.database import SessionLocal
from backend.quality.models import CallReview, ReviewFlag, ReviewVerdict
from backend.storage.base import StorageError
from backend.storage.factory import get_storage_provider, reset_storage_provider_cache
from backend.storage.local import LocalStorageProvider

HEADERS = "Customer_Name,Mobile_Number,Lead_Source,Consent_Status,City,Monthly_Bill\n"
ALL_DAY = {"window_start": "00:00:00", "window_end": "23:59:59"}

MP3 = b"ID3\x04\x00\x00\x00" + b"\x00" * 512


@pytest.fixture(autouse=True)
def storage_in_tmp_path(tmp_path, monkeypatch):
    """Recordings go to a temporary directory, never the configured store."""
    from backend.ai.factory import reset_ai_provider_cache
    from backend.telephony.factory import reset_provider_cache

    monkeypatch.setattr("backend.core.config.settings.storage_dir", str(tmp_path))
    reset_storage_provider_cache()
    reset_provider_cache()
    reset_ai_provider_cache()
    yield
    reset_storage_provider_cache()
    reset_provider_cache()
    reset_ai_provider_cache()


@pytest.fixture
def qualified_call(client, admin_headers):
    """A call that has been through a conversation, so there is work to review."""
    csv = HEADERS + "Ramesh,9876543210,WEBSITE,YES,Miyapur,7500\n"
    upload = client.post(
        "/leads/uploads", headers=admin_headers,
        files={"file": ("leads.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()
    campaign = client.post(
        "/campaigns", headers=admin_headers,
        json={"name": "Q", "concurrency": 2, "max_attempts": 3, **ALL_DAY},
    ).json()
    client.post(f"/campaigns/{campaign['id']}/leads", headers=admin_headers,
                json={"upload_id": upload["id"]})
    client.post(f"/campaigns/{campaign['id']}/start", headers=admin_headers)
    client.post(f"/campaigns/{campaign['id']}/dispatch", headers=admin_headers)
    call = client.get("/calls", headers=admin_headers).json()[0]

    conversation = client.post("/ai/conversations", headers=admin_headers,
                               json={"call_id": call["id"]}).json()
    client.post(f"/ai/conversations/{conversation['conversation_id']}/turn",
                headers=admin_headers, json={"text": "I want a site survey"})
    return client.get(f"/calls/{call['id']}", headers=admin_headers).json()


# --- storage ----------------------------------------------------------------


def test_local_storage_round_trips(tmp_path):
    store = LocalStorageProvider(str(tmp_path))
    store.put("calls/2026/01/01/call-1.mp3", MP3, content_type="audio/mpeg")

    assert store.exists("calls/2026/01/01/call-1.mp3")
    assert store.get("calls/2026/01/01/call-1.mp3") == MP3

    store.delete("calls/2026/01/01/call-1.mp3")
    assert not store.exists("calls/2026/01/01/call-1.mp3")
    store.delete("calls/2026/01/01/call-1.mp3")  # deleting twice is fine


def test_local_storage_refuses_to_escape_its_directory(tmp_path):
    store = LocalStorageProvider(str(tmp_path / "store"))
    with pytest.raises(StorageError):
        store.put("../../etc/cron.d/evil", b"x", content_type="audio/mpeg")


def test_reading_a_missing_object_is_an_error(tmp_path):
    with pytest.raises(StorageError):
        LocalStorageProvider(str(tmp_path)).get("nope.mp3")


def test_a_partial_write_is_never_visible(tmp_path):
    """put() moves the file into place, so a reader never sees half a recording."""
    store = LocalStorageProvider(str(tmp_path))
    store.put("call.mp3", MP3, content_type="audio/mpeg")
    store.put("call.mp3", MP3 + b"more", content_type="audio/mpeg")
    assert store.get("call.mp3") == MP3 + b"more"
    assert not list(tmp_path.glob("*.part"))


# --- recordings on a call ----------------------------------------------------


def test_a_recording_is_stored_and_played_back(client, admin_headers, qualified_call):
    call_id = qualified_call["id"]
    response = client.put(
        f"/quality/calls/{call_id}/recording", headers=admin_headers,
        files={"file": ("call.mp3", io.BytesIO(MP3), "audio/mpeg")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["size_bytes"] == len(MP3)

    played = client.get(f"/quality/calls/{call_id}/recording", headers=admin_headers)
    assert played.status_code == 200
    assert played.content == MP3
    assert played.headers["content-type"].startswith("audio/mpeg")
    # Customer voice data must not sit in a shared cache.
    assert "no-store" in played.headers["cache-control"]


def test_playing_a_recording_is_audited(client, admin_headers, qualified_call):
    """MVP section 32 lists recording permissions; section 31 wants user actions
    on the record. Who listened to a customer has to be answerable."""
    call_id = qualified_call["id"]
    client.put(f"/quality/calls/{call_id}/recording", headers=admin_headers,
               files={"file": ("call.mp3", io.BytesIO(MP3), "audio/mpeg")})
    client.get(f"/quality/calls/{call_id}/recording", headers=admin_headers)

    with SessionLocal() as db:
        actions = [row.action for row in db.query(AuditLog).all()]
    assert "RECORDING_PLAYED" in actions
    assert "RECORDING_STORED" in actions


def test_a_call_with_no_recording_says_so(client, admin_headers, qualified_call):
    response = client.get(f"/quality/calls/{qualified_call['id']}/recording",
                          headers=admin_headers)
    assert response.status_code == 404


def test_a_non_audio_upload_is_rejected(client, admin_headers, qualified_call):
    response = client.put(
        f"/quality/calls/{qualified_call['id']}/recording", headers=admin_headers,
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert response.status_code == 400
    assert "Unsupported" in response.json()["detail"]


def test_an_oversized_recording_is_rejected(client, admin_headers, qualified_call, monkeypatch):
    monkeypatch.setattr("backend.core.config.settings.max_recording_bytes", 100)
    response = client.put(
        f"/quality/calls/{qualified_call['id']}/recording", headers=admin_headers,
        files={"file": ("call.mp3", io.BytesIO(MP3), "audio/mpeg")},
    )
    assert response.status_code == 400
    assert "limit" in response.json()["detail"]


def test_replacing_a_recording_keeps_one_row(client, admin_headers, qualified_call):
    call_id = qualified_call["id"]
    for _ in range(2):
        assert client.put(
            f"/quality/calls/{call_id}/recording", headers=admin_headers,
            files={"file": ("call.mp3", io.BytesIO(MP3), "audio/mpeg")},
        ).status_code == 200

    with SessionLocal() as db:
        attempt = db.get(CallAttempt, call_id)
        assert attempt.recording is not None
        from backend.calls.models import CallRecording
        assert db.query(CallRecording).count() == 1


def test_the_storage_key_is_dated_so_retention_can_sweep_it(client, admin_headers,
                                                            qualified_call):
    with SessionLocal() as db:
        attempt = db.get(CallAttempt, qualified_call["id"])
        key = storage_key(attempt, "audio/mpeg")
    assert key.startswith("calls/")
    assert key.endswith(f"call-{qualified_call['id']}.mp3")


def test_the_provider_recording_is_not_fetched_unless_enabled(client, admin_headers,
                                                              qualified_call):
    """Downloading from a URL a webhook supplied is a network call; it stays off
    until somebody turns it on."""
    with SessionLocal() as db:
        attempt = db.get(CallAttempt, qualified_call["id"])
        from backend.calls.recordings import fetch_from_provider
        assert fetch_from_provider(db, attempt, "https://example.invalid/rec.mp3") is None


# --- review ------------------------------------------------------------------


def test_the_review_queue_shows_answered_calls_awaiting_review(client, admin_headers,
                                                               qualified_call):
    queue = client.get("/quality/calls?reviewed=false", headers=admin_headers).json()
    assert any(item["call_id"] == qualified_call["id"] for item in queue)

    item = next(i for i in queue if i["call_id"] == qualified_call["id"])
    assert item["customer_name"] == "Ramesh"
    assert item["review_count"] == 0
    assert item["has_recording"] is False


def test_the_review_screen_shows_the_whole_call(client, admin_headers, qualified_call):
    """MVP section 29: recording, transcript, extracted fields, summary, score
    and disposition, in one place."""
    detail = client.get(f"/quality/calls/{qualified_call['id']}",
                        headers=admin_headers).json()

    assert detail["customer_name"] == "Ramesh"
    assert detail["disposition"] == "SITE_SURVEY_REQUESTED"
    assert detail["transcript"], "the transcript is what a reviewer reads"
    assert {turn["speaker"] for turn in detail["transcript"]} == {"AI", "CUSTOMER"}
    assert detail["lead_score"] is not None
    assert detail["classification"] in ("HOT", "WARM", "COLD", "UNQUALIFIED")
    assert detail["summary"]
    assert "has_recording" in detail


def test_a_correct_verdict_is_recorded(client, admin_headers, qualified_call):
    response = client.post(f"/quality/calls/{qualified_call['id']}/review",
                           headers=admin_headers, json={"verdict": "CORRECT"})
    assert response.status_code == 201, response.text
    assert response.json()["flags"] == []

    queue = client.get("/quality/calls?reviewed=true", headers=admin_headers).json()
    assert any(item["call_id"] == qualified_call["id"] for item in queue)


def test_an_incorrect_verdict_must_name_a_fault(client, admin_headers, qualified_call):
    """An unexplained "wrong" cannot be counted, and counting is the point."""
    response = client.post(f"/quality/calls/{qualified_call['id']}/review",
                           headers=admin_headers, json={"verdict": "INCORRECT"})
    assert response.status_code == 400
    assert "flag" in response.json()["detail"].lower()


def test_a_correct_verdict_cannot_also_carry_faults(client, admin_headers, qualified_call):
    response = client.post(
        f"/quality/calls/{qualified_call['id']}/review", headers=admin_headers,
        json={"verdict": "CORRECT", "flags": ["WRONG_LANGUAGE"]},
    )
    assert response.status_code == 400


def test_all_five_mvp_fault_codes_are_accepted(client, admin_headers, qualified_call):
    response = client.post(
        f"/quality/calls/{qualified_call['id']}/review", headers=admin_headers,
        json={"verdict": "INCORRECT",
              "flags": [flag.value for flag in ReviewFlag],
              "note": "Heard the bill as 6000 instead of 7000."},
    )
    assert response.status_code == 201, response.text
    assert set(response.json()["flags"]) == {flag.value for flag in ReviewFlag}


def test_a_reviewer_changing_their_mind_replaces_their_review(client, admin_headers,
                                                              qualified_call):
    call_id = qualified_call["id"]
    client.post(f"/quality/calls/{call_id}/review", headers=admin_headers,
                json={"verdict": "CORRECT"})
    client.post(f"/quality/calls/{call_id}/review", headers=admin_headers,
                json={"verdict": "INCORRECT", "flags": ["WRONG_LEAD_SCORE"]})

    with SessionLocal() as db:
        reviews = db.query(CallReview).filter_by(call_attempt_id=call_id).all()
    assert len(reviews) == 1
    assert reviews[0].verdict is ReviewVerdict.INCORRECT


def test_the_summary_counts_what_reviewers_found(client, admin_headers, qualified_call):
    client.post(f"/quality/calls/{qualified_call['id']}/review", headers=admin_headers,
                json={"verdict": "INCORRECT", "flags": ["WRONG_TRANSCRIPTION",
                                                        "WRONG_LEAD_SCORE"]})

    summary = client.get("/quality/summary", headers=admin_headers).json()
    assert summary["reviews"] == 1
    assert summary["incorrect"] == 1
    assert summary["accuracy_percent"] == 0.0
    assert summary["flags"]["WRONG_TRANSCRIPTION"] == 1
    assert summary["flags"]["WRONG_LEAD_SCORE"] == 1
    assert summary["flags"]["WRONG_LANGUAGE"] == 0
    # Coverage matters as much as accuracy: a perfect score over two calls
    # says nothing about the platform.
    assert summary["reviewable_calls"] >= 1
    assert summary["coverage_percent"] > 0


def test_unanswered_calls_are_not_queued_for_review(client, admin_headers, qualified_call):
    """There is nothing to judge in a call nobody picked up."""
    with SessionLocal() as db:
        attempt = db.get(CallAttempt, qualified_call["id"])
        attempt.disposition = Disposition.NO_ANSWER
        db.commit()

    queue = client.get("/quality/calls", headers=admin_headers).json()
    assert all(item["call_id"] != qualified_call["id"] for item in queue)


# --- access control ----------------------------------------------------------


def test_executives_cannot_reach_recordings_or_reviews(client, executive_headers,
                                                       qualified_call):
    """Recording permissions (MVP section 32): a call is a customer conversation,
    not something everyone signed in can play back."""
    call_id = qualified_call["id"]
    assert client.get("/quality/calls", headers=executive_headers).status_code == 403
    assert client.get(f"/quality/calls/{call_id}", headers=executive_headers).status_code == 403
    assert client.get(f"/quality/calls/{call_id}/recording",
                      headers=executive_headers).status_code == 403
    assert client.post(f"/quality/calls/{call_id}/review", headers=executive_headers,
                       json={"verdict": "CORRECT"}).status_code == 403


def test_quality_requires_authentication(client):
    assert client.get("/quality/calls").status_code == 401
    assert client.get("/quality/summary").status_code == 401


def test_an_unknown_call_is_a_404(client, admin_headers):
    assert client.get("/quality/calls/99999", headers=admin_headers).status_code == 404


# --- retention (MVP section 32) ----------------------------------------------


def test_retention_deletes_old_audio_but_keeps_the_call(client, admin_headers,
                                                        qualified_call):
    """Voice data ages out; the call, transcript and disposition do not — they
    are what reporting and compliance history are built on."""
    import datetime as dt

    from backend.calls.recordings import purge_expired_recordings

    call_id = qualified_call["id"]
    client.put(f"/quality/calls/{call_id}/recording", headers=admin_headers,
               files={"file": ("call.mp3", io.BytesIO(MP3), "audio/mpeg")})

    with SessionLocal() as db:
        attempt = db.get(CallAttempt, call_id)
        key = attempt.recording.storage_key
        attempt.recording.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=120)
        db.commit()

    assert get_storage_provider().exists(key)

    with SessionLocal() as db:
        assert purge_expired_recordings(db, older_than_days=90) == 1
        db.commit()

    assert not get_storage_provider().exists(key), "the audio should be gone"
    with SessionLocal() as db:
        attempt = db.get(CallAttempt, call_id)
        assert attempt is not None, "the call itself must survive"
        assert attempt.recording is None
        assert attempt.disposition is not None


def test_retention_keeps_recent_audio(client, admin_headers, qualified_call):
    from backend.calls.recordings import purge_expired_recordings

    client.put(f"/quality/calls/{qualified_call['id']}/recording", headers=admin_headers,
               files={"file": ("call.mp3", io.BytesIO(MP3), "audio/mpeg")})
    with SessionLocal() as db:
        assert purge_expired_recordings(db, older_than_days=90) == 0


def test_retention_off_by_default_keeps_everything(client, admin_headers, qualified_call):
    """Zero means keep: how long to hold customer voice data is Swaraj's call,
    not a default this code should make."""
    from backend.calls.recordings import purge_expired_recordings
    from backend.core.config import settings

    assert settings.recording_retention_days == 0
    client.put(f"/quality/calls/{qualified_call['id']}/recording", headers=admin_headers,
               files={"file": ("call.mp3", io.BytesIO(MP3), "audio/mpeg")})
    with SessionLocal() as db:
        assert purge_expired_recordings(db, older_than_days=0) == 0
        attempt = db.get(CallAttempt, qualified_call["id"])
        assert attempt.recording is not None


def test_a_missing_file_still_clears_the_row(client, admin_headers, qualified_call):
    """If the object is already gone, the row must not survive claiming audio
    that cannot be played."""
    import datetime as dt

    from backend.calls.recordings import purge_expired_recordings

    call_id = qualified_call["id"]
    client.put(f"/quality/calls/{call_id}/recording", headers=admin_headers,
               files={"file": ("call.mp3", io.BytesIO(MP3), "audio/mpeg")})

    with SessionLocal() as db:
        attempt = db.get(CallAttempt, call_id)
        get_storage_provider().delete(attempt.recording.storage_key)
        attempt.recording.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=200)
        db.commit()

    with SessionLocal() as db:
        assert purge_expired_recordings(db, older_than_days=30) == 1
        db.commit()
    with SessionLocal() as db:
        assert db.get(CallAttempt, call_id).recording is None

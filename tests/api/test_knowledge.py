"""Curated knowledge base and retrieval (MVP section 18).

The property that matters most here is the approval gate: an unapproved
document must never reach a customer, however good a match it is.
"""

import pytest

from backend.core.database import SessionLocal
from backend.knowledge import service as knowledge
from backend.knowledge.chunking import chunk_document
from backend.knowledge.embeddings import MockEmbeddingProvider, cosine_similarity
from backend.knowledge.factory import reset_embedding_provider_cache
from backend.knowledge.loader import KNOWLEDGE_DIR, load_directory, parse_file
from backend.knowledge.models import CATEGORIES, KnowledgeDocument
from backend.leads.models import ServiceType

SUBSIDY = """## PM Surya Ghar

The central government scheme supporting rooftop solar on residential
properties. Registration happens on the national portal and the amount is paid
to the customer's bank account after commissioning.
"""

CLEANING = """## Panel cleaning

Dust on the glass blocks light and output falls gradually. Regular cleaning is
the cheapest thing a customer can do for generation.
"""


@pytest.fixture(autouse=True)
def fresh_embedding_provider():
    reset_embedding_provider_cache()
    yield
    reset_embedding_provider_cache()


def put(client, headers, slug, title, category, body, service=None, expect=200):
    payload = {"slug": slug, "title": title, "category": category, "body": body}
    if service is not None:
        payload["service"] = service.value
    response = client.put(f"/knowledge/documents/{slug}", headers=headers, json=payload)
    assert response.status_code == expect, response.text
    return response.json()


def approve(client, headers, slug):
    response = client.post(f"/knowledge/documents/{slug}/approve", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def search(client, headers, query, **kwargs):
    response = client.post("/knowledge/search", headers=headers,
                           json={"query": query, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


# --- the approval gate ------------------------------------------------------


def test_an_unapproved_document_is_never_retrieved(client, admin_headers):
    """MVP section 18: only approved company information enters production RAG."""
    document = put(client, admin_headers, "subsidy", "PM Surya Ghar", "pm-surya-ghar", SUBSIDY)
    assert document["is_approved"] is False
    assert document["passages"] > 0, "the document was indexed"

    assert search(client, admin_headers, "pm surya ghar subsidy scheme") == []

    approve(client, admin_headers, "subsidy")
    hits = search(client, admin_headers, "pm surya ghar subsidy scheme")
    assert hits, "an approved document should be retrievable"
    assert hits[0]["slug"] == "subsidy"


def test_editing_an_approved_document_withdraws_its_approval(client, admin_headers):
    """Approval is a person vouching for specific words. Change the words and
    nobody has vouched for the new ones."""
    put(client, admin_headers, "subsidy", "PM Surya Ghar", "pm-surya-ghar", SUBSIDY)
    approve(client, admin_headers, "subsidy")
    assert search(client, admin_headers, "pm surya ghar subsidy")

    edited = put(client, admin_headers, "subsidy", "PM Surya Ghar", "pm-surya-ghar",
                 SUBSIDY + "\n## Extra\n\nSomething a curator has not read.\n")
    assert edited["is_approved"] is False
    assert search(client, admin_headers, "pm surya ghar subsidy") == []


def test_retitling_without_touching_the_body_keeps_approval(client, admin_headers):
    """Only a change to the content withdraws approval — otherwise a typo fix
    in a title would silently pull the document out of service."""
    put(client, admin_headers, "subsidy", "PM Surya Ghar", "pm-surya-ghar", SUBSIDY)
    approve(client, admin_headers, "subsidy")

    renamed = put(client, admin_headers, "subsidy", "PM Surya Ghar scheme",
                  "pm-surya-ghar", SUBSIDY)
    assert renamed["is_approved"] is True


def test_withdrawing_approval_takes_a_document_out_of_service(client, admin_headers):
    put(client, admin_headers, "subsidy", "PM Surya Ghar", "pm-surya-ghar", SUBSIDY)
    approve(client, admin_headers, "subsidy")
    assert client.post("/knowledge/documents/subsidy/withdraw",
                       headers=admin_headers).status_code == 200
    assert search(client, admin_headers, "pm surya ghar subsidy") == []


# --- retrieval quality ------------------------------------------------------


def test_the_relevant_document_ranks_first(client, admin_headers):
    put(client, admin_headers, "subsidy", "PM Surya Ghar", "pm-surya-ghar", SUBSIDY)
    put(client, admin_headers, "cleaning", "Panel cleaning", "maintenance", CLEANING)
    approve(client, admin_headers, "subsidy")
    approve(client, admin_headers, "cleaning")

    assert search(client, admin_headers, "dust on panels cleaning")[0]["slug"] == "cleaning"
    assert search(client, admin_headers, "government scheme portal")[0]["slug"] == "subsidy"


def test_an_unrelated_question_retrieves_nothing(client, admin_headers):
    """Below the similarity floor the AI is told nothing rather than something
    irrelevant, and falls back to offering to have the team confirm."""
    put(client, admin_headers, "cleaning", "Panel cleaning", "maintenance", CLEANING)
    approve(client, admin_headers, "cleaning")

    assert search(client, admin_headers, "cricket match tickets Hyderabad") == []


def test_service_specific_documents_do_not_leak_across_services(client, admin_headers):
    put(client, admin_headers, "agri", "Solar pumps", "agriculture",
        "## Solar pumps\n\nPump horsepower and the water source decide the system.\n",
        service=ServiceType.AGRICULTURE_SOLAR)
    put(client, admin_headers, "general", "About Swaraj", "company",
        "## About\n\nSwaraj installs and maintains solar across Telangana.\n")
    approve(client, admin_headers, "agri")
    approve(client, admin_headers, "general")

    slugs = {hit["slug"] for hit in
             search(client, admin_headers, "pump horsepower water source",
                    service=ServiceType.RESIDENTIAL_SOLAR.value)}
    assert "agri" not in slugs, "an agriculture document answered a residential call"

    slugs = {hit["slug"] for hit in
             search(client, admin_headers, "pump horsepower water source",
                    service=ServiceType.AGRICULTURE_SOLAR.value)}
    assert "agri" in slugs


def test_chunks_from_another_embedding_model_are_ignored(client, admin_headers):
    """Vectors from two models are not comparable; ranking across them returns
    confident nonsense. Stale chunks stay put but unused until reindexed."""
    put(client, admin_headers, "cleaning", "Panel cleaning", "maintenance", CLEANING)
    approve(client, admin_headers, "cleaning")
    assert search(client, admin_headers, "dust on panels")

    with SessionLocal() as db:
        document = db.query(KnowledgeDocument).filter_by(slug="cleaning").one()
        for chunk in document.chunks:
            chunk.embedding_model = "some-other-model-v9"
        db.commit()

    assert search(client, admin_headers, "dust on panels") == []

    response = client.post("/knowledge/reindex", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["reindexed"] == 1
    assert search(client, admin_headers, "dust on panels")


# --- access control ---------------------------------------------------------


def test_only_curators_can_write_or_approve(client, operator_headers):
    response = client.put("/knowledge/documents/x", headers=operator_headers, json={
        "slug": "x", "title": "X", "category": "company", "body": SUBSIDY})
    assert response.status_code == 403
    assert client.post("/knowledge/documents/x/approve",
                       headers=operator_headers).status_code == 403


def test_knowledge_requires_authentication(client):
    assert client.post("/knowledge/search", json={"query": "subsidy"}).status_code == 401
    assert client.get("/knowledge/documents").status_code == 401


def test_an_unknown_category_is_rejected(client, admin_headers):
    response = client.put("/knowledge/documents/x", headers=admin_headers, json={
        "slug": "x", "title": "X", "category": "not-a-category", "body": SUBSIDY})
    assert response.status_code == 422


def test_a_document_with_no_passages_cannot_be_approved(client, admin_headers):
    response = client.put("/knowledge/documents/empty", headers=admin_headers, json={
        "slug": "empty", "title": "Empty", "category": "company", "body": "   "})
    assert response.status_code == 400, response.text
    assert "empty" in response.json()["detail"].lower()


# --- the shipped content ----------------------------------------------------


def test_the_shipped_knowledge_files_load_and_are_unapproved():
    """Whatever is in knowledge/ must parse, and must arrive switched off."""
    with SessionLocal() as db:
        result = load_directory(db)
        db.commit()
        assert result["failed"] == 0, "a knowledge file failed to load"
        assert result["created"] > 0

        documents = db.query(KnowledgeDocument).all()
        assert documents
        assert all(not document.is_approved for document in documents), (
            "shipped content must not arrive pre-approved — it is drafted from "
            "the MVP document, not Swaraj's own approved material"
        )
        assert all(document.chunks for document in documents)


def test_the_shipped_knowledge_quotes_no_figures():
    """Rule 1 of this codebase: the approved solar engine owns every number.

    Content the AI reads back to a customer is a way around that rule, so the
    curated files stay free of prices, subsidies, capacities and payback.
    """
    import re

    money_or_size = re.compile(
        r"(₹|rs\.?\s*\d|\d+\s*(kw|kwp|mw|kva|%|per cent|percent|years? payback))",
        re.IGNORECASE,
    )
    offenders = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        for line in parse_file(path)["body"].splitlines():
            if money_or_size.search(line):
                offenders.append(f"{path.name}: {line.strip()[:80]}")
    assert not offenders, "figures found in curated knowledge:\n" + "\n".join(offenders)


def test_every_shipped_file_uses_a_known_category():
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        assert parse_file(path)["category"] in CATEGORIES, path.name


# --- units ------------------------------------------------------------------


def test_chunking_keeps_each_heading_with_its_section():
    chunks = chunk_document("Residential solar", SUBSIDY + CLEANING)
    assert len(chunks) == 2
    assert all(chunk.startswith("Residential solar — ") for chunk in chunks)


def test_the_mock_embedding_separates_unrelated_text():
    provider = MockEmbeddingProvider()
    subsidy, cleaning = provider.embed_documents(["subsidy scheme portal", "dust cleaning panels"])
    query = provider.embed_query("subsidy portal")
    assert cosine_similarity(query, subsidy) > cosine_similarity(query, cleaning)


def test_search_with_an_empty_query_returns_nothing(client, admin_headers):
    with SessionLocal() as db:
        assert knowledge.search(db, "   ") == []


# --- what actually reaches the model on a call -------------------------------

HEADERS = "Customer_Name,Mobile_Number,Lead_Source,Consent_Status,City,Monthly_Bill\n"
ALL_DAY = {"window_start": "00:00:00", "window_end": "23:59:59"}


@pytest.fixture
def answered_call(client, admin_headers):
    """A campaign with one dialled call, ready for a conversation."""
    import io

    from backend.ai.factory import reset_ai_provider_cache as _reset_ai
    from backend.telephony.factory import reset_provider_cache

    reset_provider_cache()
    _reset_ai()
    csv = HEADERS + "Ramesh,9876543210,WEBSITE,YES,Miyapur,7500\n"
    upload = client.post(
        "/leads/uploads", headers=admin_headers,
        files={"file": ("leads.csv", io.BytesIO(csv.encode()), "text/csv")},
    ).json()
    campaign = client.post(
        "/campaigns", headers=admin_headers,
        json={"name": "K", "concurrency": 2, "max_attempts": 3, **ALL_DAY},
    ).json()
    client.post(f"/campaigns/{campaign['id']}/leads", headers=admin_headers,
                json={"upload_id": upload["id"]})
    client.post(f"/campaigns/{campaign['id']}/start", headers=admin_headers)
    client.post(f"/campaigns/{campaign['id']}/dispatch", headers=admin_headers)
    yield client.get("/calls", headers=admin_headers).json()[0]
    reset_provider_cache()
    _reset_ai()


class _Recorder:
    """Stands in for the LLM so the test can read what it was actually sent."""

    name = "recorder"

    def __init__(self):
        self.messages = []

    def generate(self, *, system, messages):
        from backend.ai.base import TurnDecision

        self.messages = messages
        self.system = system
        return TurnDecision(reply="Sare andi.", language="te-IN")

    def extract(self, *, system, transcript, schema):  # pragma: no cover
        raise NotImplementedError


def _ask(client, headers, call, question, monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr("backend.ai.conversation.get_llm_provider", lambda: recorder)
    conversation = client.post("/ai/conversations", headers=headers,
                               json={"call_id": call["id"]}).json()
    response = client.post(
        f"/ai/conversations/{conversation['conversation_id']}/turn",
        headers=headers, json={"text": question},
    )
    assert response.status_code == 200, response.text
    return recorder


def test_approved_knowledge_reaches_the_model_on_a_call(
    client, admin_headers, answered_call, monkeypatch
):
    put(client, admin_headers, "cleaning", "Panel cleaning", "maintenance", CLEANING)
    approve(client, admin_headers, "cleaning")

    recorder = _ask(client, admin_headers, answered_call,
                    "why has my generation dropped, is it dust on the glass?", monkeypatch)

    context = recorder.messages[0]["content"]
    assert "Approved Swaraj information" in context
    assert "cheapest thing a customer can do" in context
    # The instruction that keeps the model inside the approved wording.
    assert "do not fill the gap yourself" in context


def test_unapproved_knowledge_never_reaches_the_model(
    client, admin_headers, answered_call, monkeypatch
):
    """The gate has to hold on the path that actually talks to customers, not
    only on the search endpoint."""
    put(client, admin_headers, "cleaning", "Panel cleaning", "maintenance", CLEANING)

    recorder = _ask(client, admin_headers, answered_call,
                    "why has my generation dropped, is it dust on the glass?", monkeypatch)

    context = recorder.messages[0]["content"]
    assert "Approved Swaraj information" not in context
    assert "cheapest thing a customer can do" not in context


def test_a_knowledge_failure_does_not_break_the_call(
    client, admin_headers, answered_call, monkeypatch
):
    """Retrieval is an enhancement. If it fails the AI keeps talking, and its
    rules already cover offering to have the team confirm."""
    put(client, admin_headers, "cleaning", "Panel cleaning", "maintenance", CLEANING)
    approve(client, admin_headers, "cleaning")

    def explode(*args, **kwargs):
        raise RuntimeError("embedding provider is down")

    monkeypatch.setattr("backend.ai.conversation.knowledge.search", explode)

    recorder = _ask(client, admin_headers, answered_call, "is it dust on the glass?", monkeypatch)
    assert "Approved Swaraj information" not in recorder.messages[0]["content"]

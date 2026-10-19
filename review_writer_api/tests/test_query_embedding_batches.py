from unittest.mock import Mock, patch

import pytest

from review_writer_api.config import RetrievalTuning
from review_writer_api.domain_services.library_index import LibraryIndexService
from review_writer_api.domain_services.sections import SectionsService
from review_writer_api.security import Principal, Role


def service():
    gateway = Mock()
    gateway.embedding_profile.return_value = {"enabled": True, "model": "m", "dimension": 2}
    gateway.embed_for_active_job.side_effect = lambda texts, **kw: {
        "model": "m", "dimension": 2, "embeddings": [[len(t), 1] for t in texts]}
    index = LibraryIndexService(None, None, vector_enabled=True, embedding_gateway=gateway)
    index.vector_store = Mock()
    index.vector_store.rank.return_value = [("chunk", 0.9)]
    return index, gateway, Principal("user-a", frozenset({Role.USER}))


def rank(index, user, query, papers=None):
    return index._semantic_ranked_ids(user, query, allowed_papers=papers or ["P1"], limit=2)


def test_75_queries_take_four_batches_and_keep_the_same_vectors_and_scoped_ranking():
    index, gateway, user = service()
    queries = [f"question {i}" for i in range(75)]
    with index.query_embedding_session(user, queries + queries):
        for query in queries:
            assert rank(index, user, query) == [("chunk", 0.9)]
            assert rank(index, user, query, ["P2"]) == [("chunk", 0.9)]
    assert gateway.embed_for_active_job.call_count == 4
    assert [len(c.args[0]) for c in gateway.embed_for_active_job.call_args_list] == [24, 24, 24, 3]
    assert index.vector_store.rank.call_count == 150
    for query, calls in zip(queries, zip(*[iter(index.vector_store.rank.call_args_list)] * 2)):
        for call, scope in zip(calls, (["P1"], ["P2"])):
            assert call.args[3] == [float(len(query)), 1.0]
            assert call.args[4] == scope


def test_scope_pin_survives_ttl_expiry_but_not_user_or_model_change():
    index, gateway, user = service()
    with index.query_embedding_session(user, [" Shared Query "]):
        with patch("review_writer_api.domain_services.library_index.time.monotonic", return_value=10**12):
            rank(index, user, "shared query")
        assert gateway.embed_for_active_job.call_count == 1
        rank(index, Principal("user-b", frozenset({Role.USER})), "shared query")
        assert gateway.embed_for_active_job.call_count == 2
        gateway.embedding_profile.return_value = {"enabled": True, "model": "other", "dimension": 2}
        rank(index, user, "shared query")
        assert gateway.embed_for_active_job.call_count == 3
    assert index._query_embedding_session.get() is None


@pytest.mark.parametrize("bad", [RuntimeError("provider failed"),
    {"model": "m", "dimension": 2, "embeddings": [[1, 0]]},
    {"model": "m", "dimension": 2, "embeddings": [[float('nan'), 1], [1, 0]]}])
def test_failed_batch_does_not_replay_as_singletons_or_cache_partial_invalid_vectors(bad):
    index, gateway, user = service()
    gateway.embed_for_active_job.side_effect = bad if isinstance(bad, Exception) else None
    if not isinstance(bad, Exception):
        gateway.embed_for_active_job.return_value = bad
    with index.query_embedding_session(user, ["one", "two"]) as state:
        assert state.get("unavailable")
        assert rank(index, user, "one") == []
        assert rank(index, user, "two") == []
    assert gateway.embed_for_active_job.call_count == 1
    assert index._cached_query_embedding(index._query_embedding_key(user, "one")) is None


def test_cancel_callback_is_not_swallowed_and_scope_is_released():
    index, gateway, user = service()
    def cancel(*args):
        raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError, match="cancelled"), index.query_embedding_session(user, ["q"], progress=cancel):
        pytest.fail("cancelled preparation cannot continue")
    assert index._query_embedding_session.get() is None
    gateway.embed_for_active_job.assert_not_called()


def test_disabled_semantic_service_makes_no_calls():
    index, gateway, user = service()
    index.vector_enabled = False
    with index.query_embedding_session(user, ["q"]):
        assert rank(index, user, "q") == []
    gateway.embed_for_active_job.assert_not_called()


def test_paid_singleton_vectors_are_reused_before_forming_a_new_batch():
    index, gateway, user = service()
    key = "embed-query:" + index._query_embedding_key(user, "old query")[:32]
    index._paid_query_responses = Mock(side_effect=lambda principal, keys: {
        key: {"model": "m", "dimension": 2, "embeddings": [[9, 1]]}} if key in keys else {})
    with index.query_embedding_session(user, ["old query", "new query"]):
        rank(index, user, "old query")
    assert gateway.embed_for_active_job.call_args.args[0] == ["new query"]


def test_paid_lookup_is_owned_project_retry_chain_and_only_successful_query_stage():
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from review_writer_api.database import Base, User, Project, AIModelRequest
    from review_writer_api.workflow_models import WorkflowJob
    from review_writer_api.job_lease_context import bind_job_lease
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        user = User(email="query-test@example.com", password_hash="test")
        session.add(user); session.flush()
        project = Project(user_id=user.id, slug="owned", topic="topic")
        other = Project(user_id=user.id, slug="other", topic="topic")
        session.add_all([project, other]); session.flush()
        def job(p, parent=None):
            return WorkflowJob(user_id=user.id, project_id=p.id, scope="project", job_type="sections.generate",
                idempotency_scope_key=str(uuid.uuid4()), idempotency_key=str(uuid.uuid4()), retry_of_job_id=parent)
        source = job(project); foreign = job(other)
        session.add_all([source, foreign]); session.flush()
        retry = job(project, source.id); session.add(retry); session.flush()
        for owner, key, status, stage in [(source,"ok","succeeded","retrieval.query.embedding"),
            (source,"running","running","retrieval.query.embedding"),
            (source,"wrong-stage","succeeded","other"), (foreign,"foreign","succeeded","retrieval.query.embedding")]:
            session.add(AIModelRequest(user_id=user.id, project_id=owner.project_id, job_id=owner.id,
                request_key=key, stage=stage, model_tier="embedding", model_name="m", request_sha256="hash",
                input_price_usd_per_million=0, cached_input_price_usd_per_million=0, output_price_usd_per_million=0,
                status=status, response_json={"model":"m","dimension":2,"embeddings":[[1,0]]}))
    index, gateway, _ = service(); index.session_factory = factory
    principal = Principal(str(user.id), frozenset({Role.USER}))
    with bind_job_lease(str(retry.id), "test", 1):
        assert set(index._paid_query_responses(principal, ["ok","running","wrong-stage","foreign"])) == {"ok"}
        assert index._paid_query_responses(Principal(str(uuid.uuid4()), frozenset({Role.USER})), ["ok"]) == {}
    assert index._paid_query_responses(principal, ["ok"]) == {}
    gateway.embed_for_active_job.assert_not_called()
    engine.dispose()


def test_package_skips_already_prepared_and_synthesis_queries_without_changing_result():
    index, gateway, user = service()
    sections = SectionsService(Mock(), Mock(), index)
    sections._owned_project = Mock(return_value=Mock(topic="Topic"))
    sections._evidence_queries = Mock(return_value=[{"websearch_query": "New query"}])
    expected = {"sections": [{"hits": [{"content": "exact original source", "evidence_key": "key"}]}]}
    sections._retrieve_evidence_package = Mock(return_value=expected)
    result = sections._evidence_package(user, "project", [
        {"section_id": "saved", "allowed_papers": ["P1"]},
        {"section_id": "new", "allowed_papers": ["P2"]},
        {"section_id": "comparison", "synthesis_contract": "v1", "allowed_papers": ["P1"]},
    ], {}, prepared_sections={"saved": {"hits": []}})
    assert result is expected
    sections._evidence_queries.assert_called_once()
    assert gateway.embed_for_active_job.call_args.args[0] == ["new query"]

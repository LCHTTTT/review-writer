from threading import Barrier, get_ident
from review_writer_core.stages.sections.execution import run_sections


def test_two_workers_finish_independently_and_coordinator_owns_events():
    barrier = Barrier(2)
    coordinator = get_ident()
    saved, phases = [], []
    def generate(task, emit):
        assert get_ident() != coordinator
        barrier.wait(timeout=3)
        emit("reviewing")
        return {"output": task["section_id"]}
    def observe(task, phase):
        assert get_ident() == coordinator
        phases.append((task["section_id"], phase))
    def save(task, result):
        assert get_ident() == coordinator
        saved.append(task["section_id"])
    run_sections([{"section_id": "A"}, {"section_id": "B"}], generate, observe, save)
    assert set(saved) == {"A", "B"}
    assert ("A", "reviewing") in phases and ("B", "reviewing") in phases


def test_dependency_failure_does_not_block_independent_chapter_or_replay_cached():
    called, saved = [], {}
    tasks = [{"section_id": "cached"}, {"section_id": "A"},
        {"section_id": "B", "depends_on_sections": ["A"]}, {"section_id": "C"}]
    def generate(task, emit):
        called.append(task["section_id"])
        if task["section_id"] == "A":
            raise RuntimeError("Unavailable")
        return {"writing": {"claims": []}}
    run_sections(tasks, generate, lambda *_: None, lambda t, r: saved.update({t["section_id"]: r}), completed={"cached": {}})
    assert set(called) == {"A", "C"}
    assert "error" in saved["A"] and "error" in saved["B"] and "error" not in saved["C"]


def test_dependency_runs_after_saved_predecessor_and_receives_context():
    saved = []
    def generate(task, emit):
        if task["section_id"] == "B":
            assert saved == ["A"]
            assert task["dependency_context"][0]["claims"] == ["verified"]
        return {"writing": {"claims": ["verified"]}}
    run_sections([{"section_id": "A"}, {"section_id": "B", "depends_on_sections": ["A"]}],
        generate, lambda *_: None, lambda t, r: saved.append(t["section_id"]))
    assert saved == ["A", "B"]

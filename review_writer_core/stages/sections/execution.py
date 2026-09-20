"""Bounded chapter execution. Only the calling coordinator publishes state."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from queue import Queue


def chapter_responsibilities(tasks):
    """One shared snapshot for writing, audit, and checkpoint invalidation."""
    return [{key: task.get(key) for key in (
        "section_id", "heading", "writing_objective", "questions_to_answer", "avoid_points",
        "primary_papers", "supporting_papers", "paper_roles", "paragraph_tasks")}
        for task in tasks]


def run_sections(tasks, generate, observe, save, *, completed=None):
    completed = completed or {}
    retained = dict(completed) if isinstance(completed, dict) else {}
    pending = {t["section_id"]: t for t in tasks if t["section_id"] not in completed}
    done, running = set(completed), set()
    events = Queue()

    def work(task):
        sid = task["section_id"]
        try:
            result = generate(deepcopy(task), lambda phase: events.put(("phase", sid, phase)))
        except Exception as exc:
            result = {"error": str(exc)[:2000]}
        events.put(("result", sid, result))

    with ThreadPoolExecutor(max_workers=2) as pool:
        while pending or running:
            for sid, task in list(pending.items()):
                if len(running) >= 2:
                    break
                dependencies = task.get("depends_on_sections") or []
                if not set(dependencies).issubset(done):
                    continue
                del pending[sid]
                running.add(sid)
                observe(task, "preparing")
                worker_task = deepcopy(task)
                worker_task["dependency_context"] = [{"section_id": dependency,
                    "paragraphs": (retained.get(dependency, {}).get("writing") or {}).get("paragraphs", []),
                    "claims": (retained.get(dependency, {}).get("writing") or {}).get("claims", [])}
                    for dependency in dependencies]
                pool.submit(work, worker_task)
            if not running:
                for task in pending.values():
                    save(task, {"error": "章节依赖尚未完成，请先恢复前置章节。"})
                break
            kind, sid, value = events.get()
            task = next(t for t in tasks if t["section_id"] == sid)
            if kind == "phase":
                observe(task, value)
            else:
                running.remove(sid)
                save(task, value)
                if "error" not in value:
                    done.add(sid)
                    retained[sid] = value

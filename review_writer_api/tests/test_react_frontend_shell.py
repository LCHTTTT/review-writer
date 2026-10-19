from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ReactFrontendShellTests(unittest.TestCase):
    def test_guide_direct_access_is_public_and_returns_the_spa_shell(self) -> None:
        import base64
        import tempfile
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from review_writer_api.app import create_app
        from review_writer_api.config import ApiSettings
        from review_writer_api.database import Base

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database_url = f"sqlite+pysqlite:///{(root / 'guide.sqlite3').as_posix()}"
            engine = create_engine(database_url, connect_args={"check_same_thread": False})
            try:
                Base.metadata.create_all(engine)
                settings = ApiSettings(review_root=root, deployment_mode="hosted", database_url=database_url,
                    public_origin="http://testserver", hosted_workspace_root=root / "workspaces",
                    credential_encryption_key=base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="),
                    job_execution_enabled=False)
                app = create_app(settings, session_factory_override=sessionmaker(bind=engine, expire_on_commit=False))
                with TestClient(app) as client:
                    self.assertEqual(401, client.get("/api/v1/me").status_code)
                    for path in ("/guide", "/guide?project=not-a-real-project"):
                        response = client.get(path)
                        expected = 200 if (ROOT / "frontend" / "dist" / "index.html").is_file() else 503
                        self.assertEqual(expected, response.status_code, response.text)
                        self.assertIn("no-store", response.headers["cache-control"])
                        self.assertIn("Content-Security-Policy", response.headers)
                        if expected == 200:
                            self.assertIn('data-review-writer-react="true"', response.text)
            finally:
                engine.dispose()

    def test_react_router_owns_every_canonical_workflow_url(self) -> None:
        source = (ROOT / "frontend" / "src" / "app" / "App.tsx").read_text(
            encoding="utf-8"
        )
        for path in (
            "/",
            "/login",
            "/guide",
            "/workspace",
            "/library",
            "/discovery",
            "/planning",
            "/sections",
            "/images",
            "/draft",
            "/final",
            "/settings",
        ):
            with self.subTest(path=path):
                self.assertIn(f'path="{path}"', source)

    def test_fastapi_serves_only_the_react_spa_and_ketcher_assets(self) -> None:
        source = (ROOT / "review_writer_api" / "app.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/login", include_in_schema=False)', source)
        self.assertIn('@app.get("/guide", include_in_schema=False)', source)
        self.assertIn('@app.get("/workspace", include_in_schema=False)', source)
        self.assertIn('app.mount(\n            "/assets/react"', source)
        self.assertIn("if react_spa_available:", source)
        self.assertIn('app.mount(\n        "/assets/ketcher"', source)
        self.assertNotIn("dashboard_response", source)
        self.assertNotIn("dashboard_page_paths", source)
        self.assertNotIn('"/assets/app"', source)
        retired_route = '@app.get("/legacy/' + 'figures"'
        self.assertNotIn(retired_route, source)
        self.assertNotIn('return dashboard_response("/figures")', source)
        editor = (
            ROOT
            / "frontend"
            / "src"
            / "features"
            / "images"
            / "editor"
            / "SvgKetcherEditor.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn('/assets/ketcher/standalone/index.html', editor)
        self.assertIn('/manual-edit', editor)

    def test_container_builds_and_tests_frontend_before_python_runtime(self) -> None:
        source = (ROOT / "Dockerfile.api").read_text(encoding="utf-8")
        self.assertIn("FROM node:24-alpine AS frontend-builder", source)
        self.assertIn("RUN npm test && npm run build", source)
        self.assertIn("COPY --from=frontend-builder /frontend/dist ./frontend/dist", source)

    def test_lan_safe_idempotency_keys_do_not_require_secure_context(self) -> None:
        client = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("export function newIdempotencyKey", client)
        offenders: list[str] = []
        for path in (ROOT / "frontend" / "src" / "features").rglob("*.tsx"):
            if "crypto.randomUUID()" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual([], offenders)

    def test_stage_transitions_stay_inside_the_spa(self) -> None:
        offenders: list[str] = []
        for path in (ROOT / "frontend" / "src").rglob("*.tsx"):
            source = path.read_text(encoding="utf-8")
            if "window.location.assign(" in source:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual([], offenders)

    def test_react_shell_preserves_project_deletion_and_figure_retry(self) -> None:
        projects = (
            ROOT / "frontend" / "src" / "features" / "projects" / "ProjectsPage.tsx"
        ).read_text(encoding="utf-8")
        selector = (
            ROOT / "frontend" / "src" / "components" / "ProjectSelector.tsx"
        ).read_text(encoding="utf-8")
        figures = (
            ROOT / "frontend" / "src" / "features" / "images" / "ImagesPage.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn('method: "DELETE"', projects)
        self.assertIn('method: "DELETE"', selector)
        self.assertIn('/api/v1/jobs/${encodeURIComponent(retryJobId)}/retry', figures)
        self.assertIn('retry_of_job_id: retryJobId', figures)

    def test_react_shell_is_the_only_workflow_frontend(self) -> None:
        index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-review-writer-react="true"', index)
        self.assertFalse((ROOT / "view" / "assets" / "dashboard").exists())
        self.assertFalse((ROOT / "review_writer_api" / "web").exists())

    def test_blueprint_generation_uses_the_blueprint_stage_revision(self) -> None:
        planning = (
            ROOT
            / "frontend"
            / "src"
            / "features"
            / "sections"
            / "ChapterPlanningWorkspace.tsx"
        ).read_text(encoding="utf-8")
        request = planning.split("const generateBlueprint", 1)[1].split(
            "const confirmBlueprint", 1
        )[0]
        self.assertIn("revision: payload.blueprint_revision", request)
        self.assertNotIn("revision: payload.matrix_revision", request)

    def test_source_selection_syncs_redraw_without_manual_confirmation(self) -> None:
        images = (
            ROOT
            / "frontend"
            / "src"
            / "features"
            / "images"
            / "ImagesPage.tsx"
        ).read_text(encoding="utf-8")
        self.assertNotIn('/images/review/confirm', images)
        self.assertIn('/images/review/sync', images)
        self.assertIn('["figures", projectId]', images)
        self.assertIn("selection_complete", images)

    def test_svg_editor_locks_the_clicked_figure_during_background_refresh(self) -> None:
        images = (
            ROOT / "frontend" / "src" / "features" / "images" / "ImagesPage.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn('const [editorFigureId, setEditorFigureId] = useState("")', images)
        self.assertIn("figureId={editorFigureId}", images)
        self.assertNotIn("editorOpen && selected?.figure_id", images)


if __name__ == "__main__":
    unittest.main()

"""Isolated display-name, storage and legacy-path regressions (no paid calls)."""
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import importlib.util

from fastapi.testclient import TestClient

from review_writer_api.app import create_app
from review_writer_api.artifact_service import ArtifactService
from review_writer_api.database import Project, database_session
from review_writer_api.repositories import HostedProjectRepository, LocalProjectRepository, ProjectOperationError
from review_writer_api.tests import test_server_provider_admin as fixtures
from review_writer_api.workflow_repository import WorkflowRepository
from review_writer_api.workspaces import HostedWorkspaceManager, WorkspaceAccessError
from review_writer_core.workspace import validate_project_id


class ProjectNameTests(unittest.TestCase):
    setUp = fixtures.ServerProviderAdminTests.setUp
    tearDown = fixtures.ServerProviderAdminTests.tearDown

    def create(self, name):
        return HostedProjectRepository(self.sessions).create_for_user(self.user.user_id,
            slug=name, topic="Test topic", taxonomy_profile="chemistry_general")

    def test_chinese_creation_api_keeps_name_and_generates_safe_id(self):
        app = create_app(self.settings, session_factory_override=self.sessions, principal_provider=lambda: self.user)
        with TestClient(app) as client:
            response = client.post("/api/v1/projects", json={"slug": "  轴手性联烯 综述  ", "topic": "Test"})
            self.assertEqual(201, response.status_code, response.text)
            record = response.json()
            self.assertEqual("轴手性联烯 综述", record["slug"])
            uuid.UUID(record["project_id"])
            listed = client.get("/api/v1/projects").json()["items"]
            self.assertEqual(record["slug"], listed[0]["slug"])

    def test_named_project_publishes_resolves_and_archives_using_only_safe_id(self):
        project = self.create("反应比较 / 方法 A")
        repo = WorkflowRepository(self.sessions)
        manager = HostedWorkspaceManager(Path(self.temporary.name) / "isolated-users")
        service = ArtifactService(repo, manager)
        run = repo.create_stage_run(self.user.user_id, project.project_id, "sections", status="running")
        staging = service.stage_run_directory(self.user.user_id, project.project_id, run.id)
        root = manager.project_path(self.user.user_id, project.storage_slug)
        self.assertTrue(staging.is_relative_to(root))
        self.assertEqual(project.project_id, root.name)
        (staging / "body.md").write_text("中文正文", encoding="utf-8")
        artifact = service.publish(self.user.user_id, project.project_id, run.id, "body.md",
            logical_name="sections/body.md", artifact_type="markdown", producer_stage="sections")
        resolved = service.resolve_owned_artifact(self.user.user_id, artifact.id)
        self.assertEqual("中文正文", resolved.path.read_text(encoding="utf-8"))
        self.assertIsNone(repo.get_artifact(self.admin.user_id, artifact.id))
        archived = service.trash_project(self.user.user_id, project.storage_slug)
        self.assertTrue(archived.is_dir())
        self.assertFalse(root.exists())
        with self.assertRaises(WorkspaceAccessError):
            manager.project_path(self.user.user_id, "../escape")

    def test_legacy_directory_and_duplicate_name_behavior_are_preserved(self):
        with database_session(self.sessions) as session:
            legacy = Project(user_id=uuid.UUID(self.user.user_id), slug="old-review", topic="Old")
            session.add(legacy)
            session.flush()
            key = str(legacy.id)
        owned = WorkflowRepository(self.sessions).get_owned_project(self.user.user_id, key)
        self.assertEqual("old-review", owned.storage_slug)
        self.create("中文综述")
        with self.assertRaises(ProjectOperationError):
            self.create(" 中文综述 ")
        for name in (" ", "坏\n名称", "x" * 97):
            with self.assertRaises(ProjectOperationError):
                self.create(name)

    def test_local_names_survive_reload_without_using_names_as_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalProjectRepository(Path(directory), user_id="local")
            project = repository.create_for_user("local", slug="中文 综述 / 对比", topic="Test", taxonomy_profile="chemistry_general")
            self.assertEqual(project.project_id, validate_project_id(project.project_id))
            restored = LocalProjectRepository(Path(directory), user_id="local").get_for_user("local", project.project_id)
            self.assertEqual("中文 综述 / 对比", restored.slug)
            with self.assertRaises(ProjectOperationError):
                repository.create_for_user("local", slug=restored.slug, topic="Test", taxonomy_profile="chemistry_general")

    def test_migration_keeps_existing_names_and_leaves_legacy_storage_unmodified(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine, text
        path = Path(__file__).resolve().parents[2] / "migrations/versions/20261011_0030_project_display_names.py"
        spec = importlib.util.spec_from_file_location("project_name_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY, slug TEXT NOT NULL)"))
                connection.execute(text("INSERT INTO projects VALUES ('old-id', 'legacy-name')"))
                with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
                    migration.upgrade()
                row = connection.execute(text("SELECT slug, storage_slug FROM projects")).one()
                self.assertEqual(("legacy-name", None), tuple(row))
                self.assertEqual("20260925_0029", migration.down_revision)
        finally:
            engine.dispose()

"""Original image job carries exact user requests; no supplier is called."""
from fastapi.testclient import TestClient
from review_writer_api.tests.figure_test_support import NativeFigureApiTestCase
from review_writer_api.domain_services.conversation_authorization import authorize_action
from review_writer_api.errors import WorkflowValidationError

class ImageParametersTests(NativeFigureApiTestCase):
    def test_original_stage_and_child_receive_edit_parameters(self):
        with TestClient(self.app) as client:
            self.confirm_review(client)
            instructions = "保留结构，布局紧凑，颜色改为蓝色"
            response = client.post(f"/api/v1/projects/{self.project_id}/figures/jobs", json={"edit_instructions": instructions, "palette": "source"}, headers=self.headers("style"))
            self.assertEqual(202, response.status_code, response.text)
            result = self.wait_job(client, response.json()["id"])
            self.assertEqual("succeeded", result["status"], result)
            record = self.app.state.workflow_repository.get_job(self.first.user_id, result["id"])
            self.assertEqual(instructions, record.payload["edit_instructions"])
            self.assertEqual("source", record.payload["palette"])
            # The original per-item resolver is the native worker's input boundary.
            figure_id = record.payload["figure_ids"][0]
            resolved = self.app.state.figures_service.resolve_redraw_item(self.first, self.project_id, record.payload, figure_id)
            self.assertEqual(instructions, resolved["edit_instructions"])

    def test_current_message_parameters_not_invented_or_silently_discarded(self):
        message = "重绘图 F1，保留结构，颜色改为蓝色"
        args = {"figure_ids": ["F1"], "edit_instructions": "保留结构，颜色改为蓝色", "palette": "source"}
        authorize_action(message, message, "images.redraw", args)
        for unsafe in ({"figure_ids": ["F1"]}, {**args, "palette": "monochrome"}, {**args, "edit_instructions": "偷偷改掉数值"}):
            with self.assertRaises(WorkflowValidationError):
                authorize_action(message, message, "images.redraw", unsafe)

"""Local evidence stages must not depend on an unused model connection."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from review_writer_api.job_handlers.draft import DraftJobHandlers


@pytest.mark.parametrize('stage',['prepare','validate_sources','plan','plan_slice','coordinate'])
def test_native_evidence_stages_skip_gateway_configuration(tmp_path,stage):
    handler=DraftJobHandlers();handler.root=Path(__file__).resolve().parents[2]
    project=tmp_path/'projects'/'isolated';first=project/'04_first_draft';first.mkdir(parents=True)
    handler._compatibility_workspace=lambda *a,**kw:(tmp_path,tmp_path,project)
    handler._write_json=lambda path,value:path.write_text(json.dumps(value),encoding='utf-8')
    configured=[];runs=[]
    def gateway(context):
        configured.append(True)
        assert stage in ('plan','plan_slice','coordinate'),'A local-only evidence stage requested model configuration'
        return {'MODEL_TEST':'local-fixture'},{}
    handler._text_gateway_environment=gateway
    def run(command,**kwargs):
        runs.append(kwargs)
        assert json.loads((first/'paragraph_revision_request.json').read_text(encoding='utf-8'))['review_revision']['stage']==stage
        (first/'paragraph_revision_result.json').write_text(json.dumps({'stage':stage}),encoding='utf-8')
    handler.runner=SimpleNamespace(run=run)
    result=handler.draft_rewrite(SimpleNamespace(cancellation_requested=lambda:False),{
        'revision_mode':'dialogue','draft_text':'Saved manuscript','dialogue':{'review_revision':{'stage':stage}}})
    assert result=={'stage':stage}
    assert len(configured)==(1 if stage in ('plan','plan_slice','coordinate') else 0)
    assert runs[0]['max_attempts']==1


@pytest.mark.parametrize('recovered',[False,True])
def test_review_uses_durable_delegation_but_recovery_is_local(tmp_path,recovered):
    handler=DraftJobHandlers();handler.root=Path(__file__).resolve().parents[2];handler.model_gateway=object()
    project=tmp_path/'projects'/'isolated';first=project/'04_first_draft';first.mkdir(parents=True)
    handler._compatibility_workspace=lambda *a,**kw:(tmp_path,tmp_path,project)
    handler._write_json=lambda path,value:path.write_text(json.dumps(value),encoding='utf-8')
    configured=[]
    def gateway(ctx):
        configured.append(True)
        assert not recovered
        return {},{}
    handler._text_gateway_environment=gateway
    def run(command,**kwargs):
        assert kwargs['env'].get('REVIEW_WRITER_DELEGATE_MODEL_CALLS')==('1' if not recovered else None)
        (first/'paragraph_revision_result.json').write_text('{}',encoding='utf-8')
    handler.runner=SimpleNamespace(run=run)
    request={'stage':'propose',**({'model_response':{'patches':[]}} if recovered else {})}
    handler.draft_rewrite(SimpleNamespace(job_type='draft.optimize',cancellation_requested=lambda:False),{
        'revision_mode':'dialogue','draft_text':'Saved manuscript','dialogue':{'review_revision':request}})
    assert len(configured)==(0 if recovered else 1)

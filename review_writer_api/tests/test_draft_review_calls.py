"""Deterministic durable model scheduling, no network or provider keys."""
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest
from review_writer_api.job_handlers.draft_review_calls import ReviewCalls,ReviewPending,ReviewUnitFailed
from review_writer_api.errors import WorkflowConflict
from review_writer_api.scientific_runner import ScientificModelDeferred


def fixture():
    state={'board':{'hash':'board'}};children={};submitted=[]
    context=SimpleNamespace(user_id='u',project_id='p',job_id='parent',repository=SimpleNamespace(get_job=lambda u,j:children.get(j)))
    def build(ctx,payload):
        r=payload['dialogue']['review_revision']
        if 'model_response' in r:
            return r['model_response']
        jid='child-'+str(len(submitted)+1);submitted.append(jid)
        children[jid]=SimpleNamespace(project_id='p',job_type='model.dispatch',payload={'parent_job_id':'parent'},
            status='running',result={},error_message='',error_code='')
        raise ScientificModelDeferred(jid)
    payload={'snapshot':{'sha256':'text'}}
    checkpoints=[]
    calls=ReviewCalls(context,state,payload,build,lambda phase:checkpoints.append(deepcopy(state)))
    return calls,state,children,submitted


def task(write,read=None):
    return {'write_ids':[write],'read_ids':read or [write]}


def test_two_slots_out_of_order_completion_and_no_replay_on_retry():
    calls,state,children,submitted=fixture()
    for key in ('a','b'):
        with pytest.raises(ReviewPending):calls.invoke('audit',call_key=key,task=task(key))
    with pytest.raises(ReviewPending):calls.invoke('audit',call_key='c',task=task('c'))
    assert len(submitted)==2
    with pytest.raises(ReviewPending):calls.invoke('audit',call_key='a',task=task('a'))
    children['child-2'].status='succeeded';children['child-2'].result={'output_text':json.dumps({'status':'pass'})}
    # Explicit retry of a parent consumes its old owned result, not a new POST.
    calls.context.job_id='new-parent'
    assert calls.invoke('audit',call_key='b',task=task('b'))=={'status':'pass'}
    assert calls.invoke('audit',call_key='b',task=task('b'))=={'status':'pass'}
    assert len(submitted)==2 and state['model_calls']['a']['status']=='waiting'


def test_read_write_dependency_is_serialized_and_input_change_fails_closed():
    calls,_,_,submitted=fixture()
    with pytest.raises(ReviewPending):calls.invoke('propose',call_key='a',task=task('a'))
    with pytest.raises(ReviewPending):calls.invoke('propose',call_key='b',task=task('b',['a']))
    assert len(submitted)==1
    with pytest.raises(WorkflowConflict):calls.invoke('propose',call_key='a',task=task('changed'))


def test_crash_after_submission_intent_does_not_replay_under_a_retry_parent():
    calls,state,_,submitted=fixture()
    class SimulatedCrash(BaseException):pass
    def crash(ctx,payload):raise SimulatedCrash()
    calls.builder=crash
    with pytest.raises(SimulatedCrash):calls.invoke('audit',call_key='a',task=task('a'))
    assert state['model_calls']['a']['status']=='submitting'
    calls.context.job_id='retry-parent'
    with pytest.raises(ReviewUnitFailed,match='提交结果未确认'):
        calls.invoke('audit',call_key='a',task=task('a'))
    assert not submitted


def test_failed_unknown_child_is_terminal_and_successful_sibling_is_kept():
    calls,state,children,submitted=fixture()
    for key in ('a','b'):
        with pytest.raises(ReviewPending):calls.invoke('audit',call_key=key,task=task(key))
    children['child-1'].status='failed';children['child-1'].error_message='Original outcome unknown; not resubmitted.'
    children['child-1'].error_code='MODEL_RESULT_UNKNOWN'
    for _ in range(2):
        with pytest.raises(ReviewUnitFailed,match='unknown'):calls.invoke('audit',call_key='a',task=task('a'))
    children['child-2'].status='succeeded';children['child-2'].result={'output_text':'{"status":"pass"}'}
    assert calls.invoke('audit',call_key='b',task=task('b'))['status']=='pass'
    assert len(submitted)==2 and state['model_calls']['a']['error_code']=='MODEL_RESULT_UNKNOWN'


@pytest.mark.parametrize('change',[{'project_id':'foreign'},{'payload':{'parent_job_id':'foreign'}}])
def test_child_ownership_is_checked_before_consuming_result(change):
    calls,_,children,_=fixture()
    with pytest.raises(ReviewPending):calls.invoke('audit',call_key='a',task=task('a'))
    for field,value in change.items():setattr(children['child-1'],field,value)
    with pytest.raises(WorkflowConflict):calls.invoke('audit',call_key='a',task=task('a'))

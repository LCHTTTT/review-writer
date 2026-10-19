"""Bounded units retain every byte and whole object; no scientific truncation."""
from copy import deepcopy
from review_writer_core.review_revision_document import document_blocks,digest
from review_writer_core.review_revision_schedule import planning_units,article_overview,scopes_conflict,MAX_UNITS
from review_writer_core.review_revision_script import execute
from review_writer_core.tests.test_review_revision_script import fixture
import json
import pytest
from review_writer_core.review_revision_schedule import validate_coordination


def long_snapshot():
    text='# Review\n\n## Abstract\n\nA qualified summary.\n\n<!-- paragraph_id: abs -->\n\n'
    for n in range(12):
        text+=f'## Section {n}\n\n'+('Complete chemical identity and boundary. '*90)+f'\n\n<!-- paragraph_id: p{n} -->\n\n'
    text+='## Conclusion\n\nKeep the uncertainty.\n\n<!-- paragraph_id: con -->\n'
    return {'text':text,'sha256':digest(text),'blocks':document_blocks(text)}


def test_units_cover_every_byte_and_object_exactly_once_with_complete_framing():
    snapshot=long_snapshot();units=planning_units(snapshot)
    assert 2<len(units)<=MAX_UNITS
    assert ''.join(snapshot['text'][u['start']:u['end']] for u in units)==snapshot['text']
    ids=[bid for u in units for bid in u['object_ids']]
    assert ids==[b['block_id'] for b in snapshot['blocks']]
    for block in snapshot['blocks']:
        unit=next(u for u in units if block['block_id'] in u['object_ids'])
        assert unit['start']<=block['start']<block['end']<=unit['end']
    overview=article_overview(snapshot)
    assert len(overview['headings'])==15
    assert [b['block_id'] for b in overview['framing_objects']]==['paragraph:abs','paragraph:con']


def test_oversized_object_is_not_cut_and_small_manuscript_stays_single_plan():
    text='Giant intact scientific object. '*10000+'\n\n<!-- paragraph_id: giant -->\n'
    snapshot={'text':text,'blocks':document_blocks(text)}
    units=planning_units(snapshot)
    assert len(units)==1 and units[0]['end']==len(text)
    assert planning_units({'text':'Short article','blocks':[]})==[]


def test_conflicts_include_read_dependencies_not_only_shared_writes():
    a={'write_ids':['a'],'read_ids':['a','shared']}
    b={'write_ids':['b'],'read_ids':['b','a']}
    c={'write_ids':['c'],'read_ids':['c','shared']}
    assert scopes_conflict(a,b) and scopes_conflict(b,a)
    assert not scopes_conflict(a,c)


def test_slice_prompt_keeps_only_assigned_body_and_bound_claim_navigation_without_mutating_board():
    project,_,loop,documents,answers,prompts=fixture();snapshot=long_snapshot()
    loop.citation_entries=lambda project:[]
    board=execute(project,{'stage':'prepare','snapshot':snapshot},loop)['board']
    board['bindings']={bid:['P1'] for bid in [b['block_id'] for b in snapshot['blocks']]}
    board['paragraph_claim_ids']={'p0':['c0'],'p11':['c11']}
    board['claim_constraints']={'c0':{'allowed_assertion':'Only proposed'},'c11':{'allowed_assertion':'Unrelated navigation'}}
    saved=deepcopy(board);unit=planning_units(snapshot)[0]
    answers.append({'tasks':[],'limitations':[]})
    execute(project,{'stage':'plan_slice','snapshot':snapshot,'board':board,'unit':unit},loop)
    data=json.loads(prompts[0][0].rsplit('\n',1)[1])
    assert data['manuscript']==snapshot['text'][unit['start']:unit['end']]
    assert len(data['manuscript'])<len(snapshot['text'])/2
    assert data['article_overview']['framing_objects'][-1]['text']=='Keep the uncertainty.'
    assert 'c0' in data['evidence_index']['claim_constraints_navigation_only']
    assert 'c11' not in data['evidence_index']['claim_constraints_navigation_only']
    assert board==saved
    assert prompts[0][1]=='Review revision plan read-1'
    for entry in data['objects']:
        assert data['manuscript'][entry['start']:entry['end']]


def test_coordinator_must_account_for_all_local_concerns_or_disclose_limitations():
    reviews={'read-1':{'status':'reviewed','plan':{'tasks':[{'concern_id':'read-1:1','problem':'Uncertain mechanism'},
                                                        {'concern_id':'read-1:2','problem':'Caption mismatch'}]}}}
    raw={'tasks':[{'concern_ids':['read-1:1']}],'limitations':[]}
    with pytest.raises(ValueError,match='遗漏'):validate_coordination(raw,reviews)
    value=validate_coordination({**raw,'unhandled_concerns':[{'concern_id':'read-1:2','reason':'Missing original caption'}]},reviews)
    assert value['limitations']==['Caption mismatch：Missing original caption']
    with pytest.raises(ValueError,match='重复'):
        validate_coordination({'tasks':[{'concern_ids':['read-1:1','read-1:1','read-1:2']}],'limitations':[]},reviews)
    with pytest.raises(ValueError,match='未知'):
        validate_coordination({'tasks':[{'concern_ids':['foreign']}],'limitations':[]},reviews)


def test_model_response_recovery_does_not_call_provider_again():
    project,snapshot,loop,_,answers,prompts=fixture()
    board=execute(project,{'stage':'prepare','snapshot':snapshot},loop)['board']
    response={'summary':'Recovered','tasks':[],'limitations':[]}
    value=execute(project,{'stage':'plan','snapshot':snapshot,'board':board,'model_response':response},loop)
    assert value==response and not prompts

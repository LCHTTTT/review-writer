"""Native stage/evidence wiring with original bytes and mocked model responses."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from review_writer_core.review_revision_script import execute,select_sources
from review_writer_core.review_revision_document import document_blocks,digest,apply_groups
from review_writer_core.review_revision import validate_plan,validate_group,audit_passed,final_passed
from review_writer_core.tests.test_review_revision import TEXT,patch


def fixture():
    documents={'P1':{'blocks':[
        {'page':1,'text':'Original title. Chemical Communications. 2015. DOI: 10.1039/example'},
        {'page':2,'text':'The mechanism is proposed rather than established; CuBr₂ supports the reported examples.'},
        {'page':3,'text':'Right after our report (ref. 6a), other studies appeared.'},
        {'page':4,'text':'Additional comparison context and preserved limitations.'},
        {'page':5,'text':'6 (a) Author, Earlier report. 2012. Organic Letters.'},
    ]},'P2':{'blocks':[]}}
    answers=[]; prompts=[]
    def model(prompt,**kwargs):
        prompts.append((prompt,kwargs['label']))
        return answers.pop(0)
    def retrieve(pid,query,doc,**kwargs):
        assert kwargs['queries']
        return [{'ref':'retrieval-alias','page':3,'text':doc['blocks'][2]['text']}] if doc['blocks'] else []
    loop=SimpleNamespace(matrix_rows=lambda p:{'P1':{'title':'Untrusted metadata title'},'P2':{'title':'Missing original'}},
        paragraph_metadata=lambda p:{},metadata_record=lambda *a:{},claim_evidence_contract=lambda p:{},claim_bound_evidence=lambda *a:{},
        load_original_source=lambda root,pid,meta:deepcopy(documents[pid]),
        paragraph_citation_binding=lambda p,b:{str(n):'P'+str(n) for n in (1,2) if '['+str(n)+']' in b['text']},
        retrieve_original_passages=retrieve,call_json_model=model)
    snapshot={'text':TEXT,'sha256':digest(TEXT),'blocks':document_blocks(TEXT)}
    return Path('isolated/projects/example'),snapshot,loop,documents,answers,prompts


def test_original_board_is_frozen_bound_and_context_restores_actual_header_and_cited_work_date():
    project,snapshot,loop,documents,_,_=fixture()
    board=execute(project,{'stage':'prepare','snapshot':snapshot},loop)['board']
    task={'paper_ids':['P1'],'read_ids':['paragraph:abs','reference:1'],'problem':'Check chronology and journal','strategy':'Read actual original context'}
    sources=select_sources(task,board,snapshot,loop)
    assert any('Chemical Communications' in s['text'] for s in sources)
    assert any('2012. Organic Letters' in s['text'] for s in sources)
    assert all(s['ref'].startswith('P1:b') and s['source_hash']==board['papers']['P1']['source_hash'] for s in sources)
    assert not board['papers']['P2']['available']
    assert any('P2' in limit for limit in board['limitations'])
    assert execute(project,{'stage':'validate_sources','snapshot':snapshot,'board':board},loop)['unchanged']
    documents['P1']['blocks'][1]['text']='Changed source bytes'
    assert not execute(project,{'stage':'validate_sources','snapshot':snapshot,'board':board},loop)['unchanged']
    assert 'Changed source bytes' not in str(board)


def test_real_stage_contract_coordinates_abstract_conclusion_and_reference_then_independently_checks():
    project,snapshot,loop,documents,answers,prompts=fixture()
    board=execute(project,{'stage':'prepare','snapshot':snapshot},loop)['board']
    request={'snapshot':snapshot,'board':board,'guidance':'Do not guess a DOI','standard':'Critical chemistry review'}
    rawtask={'category':'consistency','problem':'Mechanism certainty and journal attribution','strategy':'Qualify claims and correct the actual journal',
             'write_ids':['paragraph:abs','paragraph:con','reference:1'],'read_ids':['paragraph:abs','paragraph:p1','paragraph:con','reference:1'],'paper_ids':['P1']}
    answers.append({'summary':'Supported coordinated revision','tasks':[rawtask],'limitations':[]})
    plan=validate_plan(execute(project,{**request,'stage':'plan'},loop),snapshot['blocks'],board);task=plan['tasks'][0]
    indexed={b['block_id']:b for b in snapshot['blocks']}
    answers.append({'explanation':'原文仅提出机制，同时校正实际期刊。','disposition':'revised','patches':[
        patch(indexed['paragraph:abs'],'The mechanism is proposed. [1]',source_refs=['P1:b2']),
        patch(indexed['paragraph:con'],'Coordination remains a proposal. [1]',source_refs=['P1:b2']),
        patch(indexed['reference:1'],'[1] Author. Chemical Communications. 2015. DOI: 10.1039/example',source_refs=['P1:b1'])]})
    built=execute(project,{**request,'stage':'propose','task':task},loop)
    group=validate_group(built['proposal'],task,TEXT,snapshot['blocks'],built['sources'])
    answers.append({'status':'pass','problem_resolved':True,'preserves_core_information':True,'no_unsupported_claims':True,
        'no_scope_expansion':True,'no_direct_regressions':True,'checked_patch_ids':[p['block_id'] for p in group['patches']],
        'source_refs':['P1:b1','P1:b2'],'reason':'Independent original-source check'})
    check=execute(project,{**request,'stage':'audit','task':task,'group':group},loop)
    assert audit_passed(check,group)
    revised=apply_groups(TEXT,snapshot['blocks'],[group])
    answers.append({'status':'pass','consistent':True,'preserves_core_information':True,'checked_group_ids':[group['group_id']],'reason':'Checked the exact combination'})
    assert final_passed(execute(project,{**request,'stage':'final','groups':[group],'revised_text':revised},loop),[group])
    assert [label for _,label in prompts]==['Review revision plan','Review revision propose','Review revision audit','Review revision final']
    assert '2012. Organic Letters' in prompts[1][0]
    assert 'Untrusted metadata title' not in prompts[0][0].split('"manuscript":')[0]
    assert 'The mechanism is proposed.' in prompts[-1][0]
    assert 'Do not guess a DOI' in prompts[2][0]


def test_plan_prompt_is_compact_but_preserves_entire_manuscript_scopes_and_claim_boundaries():
    from review_writer_core.review_revision import CATEGORY_LABELS
    project,snapshot,loop,documents,answers,prompts=fixture()
    board=execute(project,{'stage':'prepare','snapshot':snapshot},loop)['board']
    boundary='Proposed coordination only; no proof or enlarged substrate scope.'
    board['claim_constraints']={'claim-1':{'claim':boundary,'allowed_assertion':boundary,
        'assertion_ceiling':'proposed','epistemic_status':'hypothesis','required_for_section':True,
        'review_reasons':['Do not upgrade certainty'],
        'evidence_refs':[{'original_text':'Large original evidence payload. '*5000}],
        'source_verification':{'reason':'Historical report. '*1000}}}
    board['paragraph_claim_ids']={'abs':['claim-1']}
    original=deepcopy(board)
    answers.append({'summary':'No changes','tasks':[],'limitations':[]})
    execute(project,{'stage':'plan','snapshot':snapshot,'board':board},loop)
    prompt=prompts[-1][0];data=json.loads(prompt.rsplit('\n',1)[1])
    assert data['manuscript']==snapshot['text']
    assert len(data['objects'])==len(snapshot['blocks'])
    for entry,block in zip(data['objects'],snapshot['blocks']):
        assert entry=={k:block[k] for k in ('block_id','kind','start','end','sha256')}
        assert data['manuscript'][entry['start']:entry['end']]==block['text']
    claims=data['evidence_index']['claim_constraints_navigation_only']
    assert claims['claim-1']['allowed_assertion']==boundary
    assert claims['claim-1']['assertion_ceiling']=='proposed'
    assert claims['claim-1']['review_reasons']==['Do not upgrade certainty']
    assert data['evidence_index']['paragraph_claim_ids_navigation_only']==board['paragraph_claim_ids']
    assert data['evidence_index']['coverage_limits']==board['limitations']
    assert data['evidence_index']['bindings']==board['bindings']
    assert 'Large original evidence payload.' not in prompt and 'Historical report.' not in prompt
    assert len(prompt)<len(json.dumps(original,ensure_ascii=False))/5
    for code,label in CATEGORY_LABELS.items():
        assert json.dumps(code)+': '+json.dumps(label) in prompt
    assert 'do NOT output the descriptive labels' in prompt
    assert board==original  # Original bytes/hashes still available for proposal/audit.


def test_tentative_ai_pdf_quote_is_marked_as_guess_before_human_agreement():
    project,snapshot,loop,documents,answers,prompts=fixture()
    board=execute(project,{'stage':'prepare','snapshot':snapshot},loop)['board']
    task={'group_id':'g','category':'accuracy','problem':'Possible parsing conflict','strategy':'Propose only a verifiable wording',
          'read_ids':['paragraph:abs'],'write_ids':['paragraph:abs'],'paper_ids':['P1']}
    answers.append({'explanation':'Tentative wording based on parsed material',
        'pdf_quote_guess':'CuBr₂','patches':[],'disposition':'blocked','limitation':'PDF has not been visually checked'})
    result=execute(project,{'stage':'propose','snapshot':snapshot,'board':board,'task':task,
        'suggest_pdf_quote':True,'suggest_paper_id':'P1'},loop)
    assert result['proposal']['pdf_quote_guess']=='CuBr₂'
    assert 'GUESS from parsed' in prompts[0][0]
    assert 'human must compare the original PDF' in prompts[0][0]


def test_author_pdf_recoordination_reads_original_page_transcription_as_attestation_not_parsed_fact():
    project,snapshot,loop,documents,answers,prompts=fixture()
    board=execute(project,{'stage':'prepare','snapshot':snapshot},loop)['board']
    bid='paragraph:abs'
    task={'group_id':'old','category':'accuracy','problem':'Possible salt subscript mismatch',
        'strategy':'Read author-confirmed original PDF page','read_ids':[bid],'write_ids':[bid],
        'paper_ids':['P1'],'pdf_check_ids':['check-id']}
    check={'id':'check-id','group_id':'old','paper_id':'P1','page':2,'pdf_sha256':'a'*64,
        'transcription':'Author read CuBr₂ from original PDF','note':'Marked PDF experiment line',
        'origin':'author_inspected_original_pdf'}
    answers.append({'summary':'Targeted correction','tasks':[task],'limitations':[]})
    value=execute(project,{'stage':'recoordinate','snapshot':snapshot,'board':board,
        'original_task':task,'confirmed_pdf_checks':[check],'previous_composition_feedback':[]},loop)
    assert value['tasks'][0]['write_ids']==[bid]
    assert prompts[0][1]=='Review revision recoordinate'
    assert 'Do NOT claim you visually read the PDF' in prompts[0][0]
    answers.append({'explanation':'Author transcription, not AI visual evidence','patches':[],'disposition':'not_needed'})
    proposed=execute(project,{'stage':'propose','snapshot':snapshot,'board':board,'task':task,
        'confirmed_pdf_checks':[check]},loop)
    author=[s for s in proposed['sources'] if s.get('origin')=='author_inspected_original_pdf']
    assert len(author)==1 and author[0]['ref']=='author-pdf:check-id'
    assert author[0]['source_hash']=='a'*64 and author[0]['page']==2
    assert 'Author read CuBr₂' in prompts[1][0]


def test_native_paragraph_entry_uses_new_revision_stages_and_rejects_retired_flags(monkeypatch):
    import importlib.util
    script=Path(__file__).resolve().parents[2]/'skills/review-first-draft-feedback-loop/scripts/revise_paragraph.py'
    monkeypatch.syspath_prepend(str(script.parent))
    spec=importlib.util.spec_from_file_location('revision_entry_fixture',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    project,snapshot,loop,*_=fixture();module.loop=loop
    assert 'board' in module.revise(project,{'review_revision':{'stage':'prepare','snapshot':snapshot}})
    with pytest.raises(ValueError): module.revise(project,{'peer_review_global':True})

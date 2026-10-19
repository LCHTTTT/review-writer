"""Exact object edits and fail-closed review-as-revision contracts."""
import pytest
from review_writer_core.review_revision_document import document_blocks,apply_groups,digest,plan_object_aliases
from review_writer_core.review_revision import validate_plan,validate_group,audit_passed,final_passed,CATEGORY_LABELS

TEXT='''# Review title

## Abstract

The mechanism is proven. [1]

<!-- paragraph_id: abs -->

## Body

The authors proposed coordination. [1]

<!-- paragraph_id: p1 -->

<!-- inserted_figure: {"caption_quality":{"status":"pending","warnings":["source_caption_missing"]}} -->
![Figure 1](/api/v1/artifacts/image/content)
*Figure 1. Reproduced from Ref. 1.*

Table 1. Results.

| System | Catalyst | Ref. |
| --- | --- | --- |
| A | — | [1] |

## Conclusion

Coordination is established. [1]

<!-- paragraph_id: con -->

<!-- citation_map: {"1":"P1","2":"P2"} -->

## References
[1] Author. Wrong journal. 2012. DOI: wrong
[2] Other author. Supporting report. 2015.
'''


def patch(block,after,**extra):
    return {'block_id':block['block_id'],'before_sha256':block['sha256'],'after':after,
            'basis':'source','source_refs':['P1:b1'],'support_ids':[],'operation':'correct',**extra}


def test_one_atomic_group_can_edit_all_article_object_types_and_caption_state():
    blocks=document_blocks(TEXT); indexed={b['block_id']:b for b in blocks}
    assert {b['kind'] for b in blocks}=={'heading','paragraph','figure','table-caption','table','reference'}
    edits={
        'paragraph:abs':'The mechanism is proposed. [1]',
        'paragraph:p1':'The authors proposed, rather than proved, coordination. [1]',
        'paragraph:con':'Coordination remains a proposal. [1]',
        'figure:1':indexed['figure:1']['text'].replace('![Figure 1]','![Proposed coordination]').replace('Figure 1. Reproduced','Figure 1. Proposed coordination. Reproduced'),
        'table:1':indexed['table:1']['text'].replace('| A | — |','| A | CuBr₂ |'),
        'reference:1':'[1] Author. Organic Letters. 2012. DOI: 10.1021/example',
        'heading:0':'# Evidence-grounded review title',
        'table-caption:1':'Table 1. Supported catalyst comparison.',
    }
    group={'patches':[patch(indexed[key],after) for key,after in edits.items()]}
    result=apply_groups(TEXT,blocks,[group])
    assert 'CuBr₂' in result and 'Organic Letters' in result
    assert 'review_revision_verified' in result and '"status": "ready"' in result
    assert '<!-- paragraph_id: abs -->' in result
    assert 'Reproduced from Ref. 1.' in result
    assert [b['block_id'] for b in document_blocks(result)]==[b['block_id'] for b in blocks]


@pytest.mark.parametrize('mutation',[
    lambda b:patch(b,b['text']+'\n\n<!-- paragraph_id: forged -->'),
    lambda b:{**patch(b,b['text']+' qualified'),'before_sha256':'wrong'},
    lambda b:patch(b,''),
    lambda b:patch(b,b['text']+' [99]'),
    lambda b:patch(b,b['text']+'\n# Expanded scope'),
])
def test_invalid_prose_changes_are_rejected(mutation):
    blocks=document_blocks(TEXT); b=next(b for b in blocks if b['block_id']=='paragraph:abs')
    with pytest.raises(ValueError): apply_groups(TEXT,blocks,[{'patches':[mutation(b)]}])


def test_no_overlap_url_rights_or_reference_identity_changes():
    blocks=document_blocks(TEXT); indexed={b['block_id']:b for b in blocks}
    for key,after in [('figure:1',indexed['figure:1']['text'].replace('/image/','/different/')),
                      ('figure:1',indexed['figure:1']['text'].replace('Reproduced','Adapted')),
                      ('reference:1',indexed['reference:1']['text'].replace('[1]','[2]'))]:
        with pytest.raises(ValueError): apply_groups(TEXT,blocks,[{'patches':[patch(indexed[key],after)]}])
    p=patch(indexed['paragraph:abs'],'The mechanism is proposed. [1]')
    with pytest.raises(ValueError): apply_groups(TEXT,blocks,[{'patches':[p]},{'patches':[p]}])


def test_narrowing_needs_manuscript_support_and_no_new_values():
    blocks=document_blocks(TEXT); b=next(b for b in blocks if b['block_id']=='paragraph:abs')
    task={'group_id':'g','category':'consistency','problem':'certainty mismatch','write_ids':[b['block_id']],
        'read_ids':[b['block_id'],'paragraph:p1']}
    p=patch(b,'The mechanism is proposed. [1]',basis='manuscript',source_refs=[],support_ids=['paragraph:p1'],operation='qualify')
    g=validate_group({'explanation':'与正文的推测性表述保持一致。','patches':[p]},task,TEXT,blocks,[])
    assert g['fingerprint']
    with pytest.raises(ValueError):
        validate_group({'explanation':'x','patches':[{**p,'after':'The mechanism gives 99% ee. [1]'}]},task,TEXT,blocks,[])
    with pytest.raises(ValueError):
        validate_group({'explanation':'x','patches':[{**p,'support_ids':[]}]},task,TEXT,blocks,[])


def test_unknown_scientific_refs_and_incomplete_audits_do_not_count_as_resolution():
    blocks=document_blocks(TEXT); b=next(b for b in blocks if b['block_id']=='paragraph:abs')
    task={'group_id':'g','category':'accuracy','problem':'certainty','read_ids':[b['block_id']],'write_ids':[b['block_id']]}
    with pytest.raises(ValueError): validate_group({'explanation':'x','patches':[patch(b,'Proposed. [1]')]},task,TEXT,blocks,[])
    g=validate_group({'explanation':'x','patches':[patch(b,'Proposed. [1]')]},task,TEXT,blocks,[{'ref':'P1:b1','text':'Original'}])
    yes={'status':'pass','problem_resolved':True,'preserves_core_information':True,'no_unsupported_claims':True,
        'no_scope_expansion':True,'no_direct_regressions':True,'checked_patch_ids':[b['block_id']],'source_refs':['P1:b1'],'reason':'Checked'}
    assert audit_passed(yes,g)
    assert not audit_passed({**yes,'source_refs':[]},g)
    assert not audit_passed({**yes,'checked_patch_ids':[]},g)
    assert not final_passed({'status':'pass','consistent':True,'preserves_core_information':True,'reason':'ok'},[g])


def test_plan_combines_shared_objects_and_never_creates_unknown_papers_or_targets():
    blocks=document_blocks(TEXT)
    task={'category':'accuracy','problem':'certainty mismatch','strategy':'qualify with original',
        'write_ids':['paragraph:abs'],'read_ids':['paragraph:p1'],'paper_ids':['P1']}
    board={'papers':{'P1':{}}}
    result=validate_plan({'summary':'One group','tasks':[task],'limitations':[]},blocks,board)
    assert result['tasks'][0]['read_ids']==['paragraph:p1','paragraph:abs']
    merged=validate_plan({'summary':'x','tasks':[task,{**task,'problem':'A second concern on the same abstract'}],'limitations':[]},blocks,board)
    assert len(merged['tasks'])==1 and 'A second concern' in merged['tasks'][0]['problem']
    for tasks in ([{**task,'paper_ids':['foreign']}],[{**task,'write_ids':['foreign']}],[{**task,'paper_ids':'P1'}]):
        with pytest.raises(ValueError): validate_plan({'summary':'x','tasks':tasks,'limitations':[]},blocks,board)


@pytest.mark.parametrize('category,label',CATEGORY_LABELS.items())
def test_plan_recovers_exact_original_prompt_labels_without_mutating_checkpoint(category,label):
    from copy import deepcopy
    task={'category':label,'problem':'Check certainty','strategy':'Qualify with evidence',
          'write_ids':['paragraph:abs'],'read_ids':['paragraph:p1'],'paper_ids':['P1']}
    raw={'summary':'x','tasks':[task],'limitations':['An unresolved source gap']}
    saved=deepcopy(raw)
    result=validate_plan(raw,document_blocks(TEXT),{'papers':{'P1':{}}})
    assert result['tasks'][0]['category']==category
    assert result['limitations']==raw['limitations']
    assert raw==saved
    task['category']=' '+category.upper()+' '
    assert validate_plan(raw,document_blocks(TEXT),{'papers':{'P1':{}}})['tasks'][0]['category']==category


@pytest.mark.parametrize('changes,message',[
    ({'category':'accuracy-ish'},'category 无效'),
    ({'category':['accuracy']},'category 无效'),
    ({'problem':''},'问题说明（problem）'),
    ({'strategy':None},'执行方案（strategy）'),
    ({'write_ids':['foreign']},'未知位置或原文来源'),
    ({'paper_ids':['foreign']},'未知位置或原文来源'),
])
def test_plan_diagnostics_name_actual_invalid_field_without_relaxing_scopes(changes,message):
    task={'category':'accuracy','problem':'Check certainty','strategy':'Qualify with evidence',
          'write_ids':['paragraph:abs'],'read_ids':['paragraph:p1'],'paper_ids':['P1'],**changes}
    with pytest.raises(ValueError,match=message):
        validate_plan({'tasks':[task],'limitations':[]},document_blocks(TEXT),{'papers':{'P1':{}}})


def generated_table_text():
    return '''Table 1. Reported examples.

| System | Ref. |
| --- | --- |
| A | [1] |

— = unavailable in the extracted records; this does not establish absence from the original paper.

<!-- reaction_table: {"section_id":"S03","projection":"section-reaction-table/3","rows":[]} -->

<!-- paragraph_id: S03-T01 -->
'''


def test_generated_table_plan_alias_is_exact_unique_and_does_not_expand_writes():
    text=generated_table_text();blocks=document_blocks(text)
    assert plan_object_aliases(text,blocks)=={'paragraph:S03-T01':'table:1'}
    task={'category':'figures/tables/references','problem':'Check table','strategy':'Correct table with original evidence',
          'write_ids':['paragraph:S03-T01'],'read_ids':['paragraph:S03-T01'],'paper_ids':['P1']}
    value={'tasks':[task,{**task,'write_ids':['table:1'],'read_ids':['table:1']}],'limitations':[]}
    result=validate_plan(value,blocks,{'papers':{'P1':{}}},markdown=text)
    assert len(result['tasks'])==1
    assert result['tasks'][0]['write_ids']==['table:1']
    assert result['tasks'][0]['read_ids']==['table:1']
    assert task['write_ids']==['paragraph:S03-T01']
    with pytest.raises(ValueError,match='write_ids/read_ids'):
        validate_plan(value,blocks,{'papers':{'P1':{}}})  # Never infer from the ID alone.


@pytest.mark.parametrize('transform',[
    lambda s:s+'\n<!-- paragraph_id: S03-T01 -->',
    lambda s:s.replace('"section_id":"S03"','"section_id":"S04"'),
    lambda s:s.replace('section-reaction-table/3','unknown'),
    lambda s:s.replace('"rows":[]','"rows":invalid-json'),
    lambda s:s.replace('— = unavailable in the extracted records; this does not establish absence from the original paper.',
                       'Unrelated scientific paragraph between table and marker.'),
    lambda s:s.replace('<!-- reaction_table:', '<!-- unrelated_metadata:'),
])
def test_table_alias_never_guesses_ambiguous_or_unrelated_marker(transform):
    text=transform(generated_table_text())
    assert plan_object_aliases(text,document_blocks(text))=={}


def test_alt_only_edit_does_not_claim_a_missing_caption_is_fixed():
    blocks=document_blocks(TEXT);figure=next(b for b in blocks if b['kind']=='figure')
    after=figure['text'].replace('![Figure 1]','![Proposed coordination]')
    updated=apply_groups(TEXT,blocks,[{'patches':[patch(figure,after)]}])
    assert 'source_caption_missing' in updated
    assert 'review_revision_verified' not in updated


@pytest.mark.parametrize('bid,after',[
    ('table-caption:1','Table 1. Results.\n## Unauthorized heading'),
    ('reference:1','[1] Author. ![Unexpected external image](https://example.test/image)'),
])
def test_non_prose_objects_cannot_smuggle_new_structure(bid,after):
    blocks=document_blocks(TEXT);block=next(b for b in blocks if b['block_id']==bid)
    with pytest.raises(ValueError):apply_groups(TEXT,blocks,[{'patches':[patch(block,after)]}])

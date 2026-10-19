from review_writer_core.review_source_context import contextual_review_evidence, bibliography_context
from review_writer_core.manuscript_coherence import audit_candidate


def test_exact_review_hint_is_reresolved_from_original_not_trusted_as_evidence():
    blocks=[{'page':1,'text':'Hydroxyl coordination is proposed, not proven.'}]
    load=lambda pid:{'blocks':blocks} if pid=='P1' else {}
    evidence=contextual_review_evidence({'evidence':[]},[
        {'paper_id':'P1','text':'Hydroxyl coordination is proposed, not proven.'},
        {'paper_id':'P1','text':'Hydroxyl coordination is proven.'},
        {'paper_id':'other-account','text':'invented evidence'}],load)
    assert len(evidence['evidence'])==1
    paper=evidence['evidence'][0]
    assert paper['source_content_hash']
    assert [s['text'] for s in paper['original_passages']]==[blocks[0]['text']]


def test_conflicting_original_versions_are_not_merged():
    import pytest
    with pytest.raises(ValueError,match='原文版本'):
        contextual_review_evidence({'evidence':[{'paper_id':'P1','source_content_hash':'old-version',
            'original_passages':[{'ref':'old','text':'Text'}]}]},[],
            lambda pid:{'blocks':[{'page':1,'text':'Current source'}]})


def test_temporal_pronoun_recovers_reference_identity_not_citing_paper_date():
    blocks=[{'page':4,'text':'6 (a) Ye et al. Org. Lett., 2012, 14, 1346.'},
            {'page':4,'text':'7 Periasamy et al. also reported the synthesis'},
            {'page':5,'text':'by using diphenylprolinol right after our report (ref. 6a); failed to reproduce it.'}]
    selected={'evidence':[{'paper_id':'P1','original_passages':[{'ref':'old','text':blocks[-1]['text']}]}]}
    result=contextual_review_evidence(selected,[],lambda pid:{'blocks':blocks})
    assert any(s['text']==blocks[0]['text'] for s in result['evidence'][0]['original_passages'])
    assert any(s['text']==blocks[1]['text'] for s in result['evidence'][0]['original_passages'])


def test_reference_checks_include_original_first_page_and_full_manuscript_entries():
    blocks=[{'page':1,'text':'Chemical Communications. DOI: 10.1039/c5cc00697'},
            {'page':2,'text':'Experimental observations.'}]
    evidence={'evidence':[{'paper_id':'P1','original_passages':[{'ref':'P1:2','text':blocks[1]['text']}]}]}
    result=contextual_review_evidence(evidence,[],lambda pid:{'blocks':blocks},bibliography=True)
    assert any(s['text']==blocks[0]['text'] for s in result['evidence'][0]['original_passages'])
    manuscript='# Body\n\nText\n\n## References\n[3] Wrong DOI, real journal.\n[5] Wrong journal.\n'
    assert bibliography_context(manuscript)=='## References\n[3] Wrong DOI, real journal.\n[5] Wrong journal.\n'
    assert bibliography_context('No references')==''


def test_focused_audit_checks_every_semantic_change_without_requiring_unrelated_baseline_sources():
    prompts=[]
    def call(prompt,**kwargs):
        prompts.append(prompt)
        return {'status':'supported','preserves_information':True,'source_refs':['r'],'reason':'Changed claim supported'}
    assert audit_candidate('Old','New',[{'ref':'r','text':'Original passage'}],call,changed_only=True)['status']=='supported'
    assert 'EVERY semantic change' in prompts[0]
    assert 'Unchanged baseline assertions are not newly verified' in prompts[0]
    audit_candidate('Old','New',[{'ref':'r','text':'Original passage'}],call)
    assert 'All candidate scientific assertions must be supported' in prompts[1]
    rejected=audit_candidate('Old','Wrong',[{'ref':'r','text':'Original passage'}],
        lambda *a,**kw:{'status':'unsupported','reason':'The referent is 2012, not 2015'},changed_only=True)
    assert rejected['status']=='unresolved'
    assert rejected['detail']=='The referent is 2012, not 2015'

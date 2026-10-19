"""SQLite/artifact acceptance of the replacement reviewer; no supplier calls."""
from copy import deepcopy
from types import SimpleNamespace
import uuid
from fastapi.testclient import TestClient
from review_writer_api.tests.figure_test_support import NativeFigureApiTestCase
from review_writer_api.tests import test_drafts_v1 as draft_support
from review_writer_api.domain_services.draft_review import review_payload,validate_review
from review_writer_api.job_handlers.draft_review import run_review
from review_writer_api.job_service import JobYieldRequested
from review_writer_api.errors import WorkflowConflict,WorkflowValidationError
from review_writer_core.review_revision_document import digest
from review_writer_core.review_revision import POLICY
from review_writer_core.review_revision_public import review_result


class DraftReviewRevisionTests(NativeFigureApiTestCase):
    prepare_draft=draft_support.DraftsV1Tests.prepare_draft
    def extra_native_workflow_overrides(self):
        overrides=draft_support.DraftsV1Tests.extra_native_workflow_overrides(self)
        previous=overrides['draft.rewrite']
        def builder(context,payload):
            if (payload.get('dialogue') or {}).get('review_revision'):
                return self.builder(payload)[0](context,payload)
            return previous(context,payload)
        overrides['draft.rewrite']=builder
        return overrides

    def setUp(self):
        self.offline_text_provider_key='offline-fixture-not-a-real-key'
        self.noop_rewrite=False; self.hard_gate_failures=[]; self.accept_rewrite_model_calls=0
        self.rewrite_source_evidence_refs=[]; self.rewrite_source_check_entry={}
        self.rewrite_unsupported_claims_before=[]; self.rewrite_unsupported_claims_after=[]
        super().setUp()

    def setup_payload(self,client):
        self.prepare_draft(client)
        self.service=self.app.state.drafts_service
        current=self.service.get(self.first,self.project_id)
        return review_payload(self.service,self.first,self.project_id,expected_draft_artifact_id=current['draft_artifact_id'])

    def context(self,payload):
        job=SimpleNamespace(result={},retry_of_job_id=None)
        context=SimpleNamespace(user_id=self.first.user_id,project_id=self.project_id,job_id=str(uuid.uuid4()),
            repository=SimpleNamespace(get_job=lambda *args:job),checkpoint=lambda:None,report_progress=lambda *args:None,
            report_partial_result=lambda value:setattr(job,'result',deepcopy(value)))
        return context,job

    def drive(self,payload,builder,context=None):
        context=context or self.context(payload)[0]
        for _ in range(80):
            try: return run_review(self.service,builder,context,payload)
            except JobYieldRequested: pass
        self.fail('Revision did not terminate')

    def builder(self,payload,*,audit_status='pass',final_status='pass',source_unchanged=True):
        b=next(b for b in payload['snapshot']['blocks'] if b['kind']=='paragraph')
        task={'group_id':'g','category':'consistency','problem':'Overbroad scope','strategy':'Qualify using the saved body',
            'write_ids':[b['block_id']],'read_ids':[b['block_id']],'paper_ids':[]}
        calls=[]
        def build(context,one):
            r=one['dialogue']['review_revision']; stage=r['stage'];calls.append(stage)
            assert r['snapshot']==payload['snapshot']
            assert r['guidance']==payload['guidance']
            if stage=='prepare':
                board={'version':'evidence-board/1','manuscript_hash':payload['snapshot']['sha256'],'papers':{},'bindings':{},'limitations':[]}
                return {'board':{**board,'hash':digest(board)}}
            if stage=='plan': return {'summary':'Limit the claim to the reported examples.','tasks':[task],'limitations':[]}
            if stage=='propose': return {'proposal':{'explanation':'Preserve all reported content; limit generality.',
                'patches':[{'block_id':b['block_id'],'before_sha256':b['sha256'],'after':b['text']+' These observations are limited to the reported examples.',
                    'basis':'manuscript','source_refs':[],'support_ids':[b['block_id']],'operation':'qualify'}]},'sources':[]}
            if stage=='audit': return {'status':audit_status,'problem_resolved':True,'preserves_core_information':True,'no_unsupported_claims':True,
                'no_scope_expansion':True,'no_direct_regressions':True,'checked_patch_ids':[b['block_id']],'source_refs':[],'reason':'Offline independent audit'}
            if stage=='final': return {'status':final_status,'consistent':True,'preserves_core_information':True,'no_direct_regressions':True,
                'checked_group_ids':[g['group_id'] for g in r['groups']],'affected_group_ids':[g['group_id'] for g in r['groups']] if final_status!='pass' else [],'reason':'Offline composition audit'}
            if stage=='validate_sources': return {'unchanged':source_unchanged,'board_hash':r['board']['hash']}
            raise AssertionError(stage)
        return build,calls

    def test_parallel_groups_keep_fast_sibling_and_publish_only_verified_changes(self):
        import json
        from review_writer_api.scientific_runner import ScientificModelDeferred
        with TestClient(self.app) as client:
            payload=self.setup_payload(client);base,_=self.builder(payload)
            blocks=[b for b in payload['snapshot']['blocks'] if b['kind']=='paragraph'][:2]
            self.assertEqual(2,len(blocks))
            context,job=self.context(payload);children={};submitted={}
            context.repository.get_job=lambda user,jid:job if jid==context.job_id else children.get(jid)
            def build(ctx,one):
                r=one['dialogue']['review_revision'];stage=r['stage']
                if stage in ('prepare','validate_sources'):return base(ctx,one)
                if stage=='plan':return {'summary':'Two independent qualifications','limitations':[],
                    'tasks':[{'category':'consistency','problem':'Qualify scope','strategy':'Keep the reported boundary',
                        'read_ids':[b['block_id']],'write_ids':[b['block_id']],'paper_ids':[]} for b in blocks]}
                if 'model_response' in r:
                    return {'proposal':r['model_response'],'sources':[]} if stage=='propose' else r['model_response']
                gid=r.get('task',{}).get('group_id','combination');key=(stage,gid)
                self.assertNotIn(key,submitted,'A paid unit was submitted twice')
                if stage=='propose':
                    b=next(b for b in blocks if b['block_id'] in r['task']['write_ids'])
                    response={'explanation':'Qualify without removing information','patches':[{'block_id':b['block_id'],
                        'before_sha256':b['sha256'],'after':b['text']+' These observations are limited to the reported examples.',
                        'basis':'manuscript','source_refs':[],'support_ids':[b['block_id']],'operation':'qualify'}]}
                elif stage=='audit':response={'status':'pass','problem_resolved':True,'preserves_core_information':True,
                    'no_unsupported_claims':True,'no_scope_expansion':True,'no_direct_regressions':True,
                    'checked_patch_ids':r['task']['write_ids'],'source_refs':[],'reason':'Independent check'}
                else:response={'status':'pass','consistent':True,'preserves_core_information':True,
                    'checked_group_ids':[g['group_id'] for g in r['groups']],'reason':'Exact combination checked'}
                jid=str(uuid.uuid4());submitted[key]=jid
                children[jid]=SimpleNamespace(project_id=self.project_id,job_type='model.dispatch',
                    payload={'parent_job_id':context.job_id},status='running',result={'output_text':json.dumps(response)},
                    error_code='',error_message='')
                raise ScientificModelDeferred(jid)
            def tick():
                try:return run_review(self.service,build,context,payload)
                except JobYieldRequested:return None
            for _ in range(8):
                tick()
                if len(submitted)==2:break
            self.assertEqual({('propose','revision-1'),('propose','revision-2')},set(submitted))
            self.assertEqual(2,len(job.result['waiting_model_job_ids']))
            children[submitted['propose','revision-2']].status='succeeded'
            for _ in range(5):
                tick()
                if ('audit','revision-2') in submitted:break
            children[submitted['audit','revision-2']].status='succeeded'
            for _ in range(5):
                tick()
                if job.result['groups']['revision-2'].get('status')=='verified':break
            self.assertEqual('verified',job.result['groups']['revision-2']['status'])
            self.assertEqual('running',children[submitted['propose','revision-1']].status)
            failed=children[submitted['propose','revision-1']];failed.status='failed'
            failed.error_code='MODEL_RESULT_UNKNOWN';failed.error_message='Original result unknown; not resubmitted.'
            for _ in range(5):
                tick()
                if ('final','combination') in submitted:break
            self.assertNotIn(('audit','revision-1'),submitted)
            children[submitted['final','combination']].status='succeeded'
            result=self.drive(payload,build,context)
            self.assertEqual('revised_with_limits',result['outcome'])
            self.assertEqual(1,result['publication']['saved_groups'])
            self.assertTrue(any('unknown' in message for message in result['limitations']))
            after=self.service.get(self.first,self.project_id)
            self.assertEqual(1,after['first_draft_md'].count('These observations are limited to the reported examples.'))
            self.assertEqual(4,len(submitted))

    def test_large_manuscript_uses_units_then_one_coordinator_and_retains_coverage_gaps(self):
        from review_writer_core.tests.test_review_revision_schedule import long_snapshot
        with TestClient(self.app) as client:
            initial=self.setup_payload(client)
            self.service.save_text(self.first,self.project_id,text=long_snapshot()['text'],revision=initial['source_draft_revision'])
            current=self.service.get(self.first,self.project_id)
            payload=review_payload(self.service,self.first,self.project_id,expected_draft_artifact_id=current['draft_artifact_id'])
            base,calls=self.builder(payload);context,job=self.context(payload)
            def build(ctx,one):
                r=one['dialogue']['review_revision'];stage=r['stage']
                if stage=='plan_slice':
                    calls.append(stage)
                    if r['unit']['unit_id']=='read-1':return {'tasks':'invalid','limitations':[]}
                    return {'tasks':[],'limitations':[],'summary':'Assigned unit reviewed'}
                if stage=='coordinate':
                    calls.append(stage)
                    self.assertTrue(any(v['status']=='limited' for v in r['unit_reviews'].values()))
                    return {'summary':'Remaining units reviewed','tasks':[],'limitations':[]}
                return base(ctx,one)
            result=self.drive(payload,build,context)
            self.assertGreater(calls.count('plan_slice'),1);self.assertEqual(1,calls.count('coordinate'))
            self.assertNotIn('plan',calls)
            self.assertEqual('limited',result['outcome']);self.assertFalse(result['publication']['changed'])
            self.assertTrue(any('read-1' in value for value in result['limitations']))
            self.assertEqual(payload['source_draft_artifact_id'],self.service.get(self.first,self.project_id)['draft_artifact_id'])

    def test_atomic_delivery_real_artifact_history_and_public_comparison(self):
        with TestClient(self.app) as client:
            payload=self.setup_payload(client); before=self.service.get(self.first,self.project_id)
            build,calls=self.builder(payload); result=self.drive(payload,build)
            after=self.service.get(self.first,self.project_id)
            self.assertEqual('revised',result['outcome']); self.assertTrue(result['publication']['changed'])
            self.assertNotEqual(before['draft_artifact_id'],after['draft_artifact_id'])
            self.assertIn('limited to the reported examples',after['first_draft_md'])
            old=self.service.artifacts.resolve_owned_artifact(self.first.user_id,before['draft_artifact_id']).path.read_text(encoding='utf-8')
            self.assertEqual(before['first_draft_md'],old)
            self.assertEqual(['prepare','plan','propose','audit','final','validate_sources'],calls)
            public=review_result(result,payload)
            self.assertNotIn('board',public); self.assertNotIn('groups',public)
            self.assertTrue(public['revisions'][0]['patches'][0]['before'])
            self.assertEqual('saved',public['revisions'][0]['status'])

    def test_rejected_or_incomplete_audit_never_overwrites_and_is_bounded(self):
        with TestClient(self.app) as client:
            payload=self.setup_payload(client); build,calls=self.builder(payload,audit_status='fail')
            result=self.drive(payload,build)
            self.assertEqual('limited',result['outcome']); self.assertFalse(result['publication']['changed'])
            self.assertEqual(2,calls.count('propose'));self.assertEqual(2,calls.count('audit'))
            self.assertNotIn('final',calls)
            self.assertEqual(payload['source_draft_artifact_id'],self.service.get(self.first,self.project_id)['draft_artifact_id'])

    def test_failed_final_combination_is_not_saved(self):
        with TestClient(self.app) as client:
            payload=self.setup_payload(client);build,calls=self.builder(payload,final_status='fail')
            result=self.drive(payload,build)
            self.assertEqual('limited',result['outcome']);self.assertFalse(result['publication']['changed'])
            self.assertEqual(1,calls.count('final')); self.assertNotIn('validate_sources',calls)

    def test_checkpoints_resume_without_replaying_finished_model_work(self):
        with TestClient(self.app) as client:
            payload=self.setup_payload(client);build,calls=self.builder(payload)
            context,job=self.context(payload)
            for _ in range(4):
                with self.assertRaises(JobYieldRequested): run_review(self.service,build,context,payload)
            self.assertEqual(['prepare','plan','propose','audit'],calls)
            result=self.drive(payload,build,context)
            self.assertTrue(result['publication']['changed']);self.assertEqual(1,calls.count('propose'))
            recovered=self.drive(payload,lambda *args:self.fail('Published work was replayed'),context)
            self.assertEqual('saved',recovered['revisions'][0]['status'])
            saved_id=result['publication']['draft_artifact_id']
            after=self.service.get(self.first,self.project_id)
            self.service.save_text(self.first,self.project_id,text=after['first_draft_md']+'\nAuthor follow-up.\n',revision=after['revision'])
            recovered=self.drive(payload,lambda *args:self.fail('Committed work was replayed after a manual edit'),context)
            self.assertEqual(saved_id,recovered['publication']['draft_artifact_id'])
            self.assertIn('Author follow-up.',self.service.get(self.first,self.project_id)['first_draft_md'])

    def test_retry_recovers_descriptive_category_checkpoint_without_replaying_plan(self):
        with TestClient(self.app) as client:
            payload=self.setup_payload(client);base,calls=self.builder(payload)
            def build(context,one):
                value=base(context,one)
                if one['dialogue']['review_revision']['stage']=='plan':
                    value['tasks'][0]['category']='abstract-body-conclusion consistency'
                return value
            prior_context,prior=self.context(payload)
            for _ in range(2):
                with self.assertRaises(JobYieldRequested): run_review(self.service,build,prior_context,payload)
            self.assertEqual(['prepare','plan'],calls)
            saved=deepcopy(prior.result)
            prior.project_id=self.project_id;prior.payload=payload
            context,job=self.context(payload);job.retry_of_job_id=prior_context.job_id
            context.repository.get_job=lambda user,job_id:prior if job_id==prior_context.job_id else job
            result=self.drive(payload,build,context)
            self.assertTrue(result['publication']['changed'])
            self.assertEqual(['prepare','plan','propose','audit','final','validate_sources'],calls)
            self.assertEqual('consistency',result['plan']['tasks'][0]['category'])
            self.assertEqual(saved,prior.result)  # Historical report is not rewritten.

    def test_bad_plan_checkpoint_is_diagnosed_without_paid_replay_or_manuscript_write(self):
        with TestClient(self.app) as client:
            payload=self.setup_payload(client);base,calls=self.builder(payload)
            context,job=self.context(payload)
            for _ in range(2):
                with self.assertRaises(JobYieldRequested): run_review(self.service,base,context,payload)
            job.result['plan_raw']['tasks'][0]['category']='not-a-review-category'
            with self.assertRaisesRegex(WorkflowValidationError,'category 无效'):
                self.drive(payload,lambda *args:self.fail('Invalid checkpoint replayed a model call'),context)
            self.assertEqual(payload['source_draft_artifact_id'],self.service.get(self.first,self.project_id)['draft_artifact_id'])

    def test_source_change_and_saved_manuscript_conflict_preserve_original(self):
        with TestClient(self.app) as client:
            payload=self.setup_payload(client);build,calls=self.builder(payload,source_unchanged=False)
            with self.assertRaises(WorkflowConflict): self.drive(payload,build)
            self.assertEqual(payload['source_draft_artifact_id'],self.service.get(self.first,self.project_id)['draft_artifact_id'])
            changed=deepcopy(payload);changed['snapshot']['sha256']='corrupt'
            with self.assertRaises(WorkflowValidationError): validate_review(self.service,self.first,self.project_id,changed)
            stale=deepcopy(payload);stale['source_draft_revision']+=1
            with self.assertRaises(WorkflowConflict): validate_review(self.service,self.first,self.project_id,stale)

    def test_new_api_requires_snapshot_rejects_old_controls_and_binds_idempotency(self):
        with TestClient(self.app) as client:
            payload=self.setup_payload(client);url=f'/api/v1/projects/{self.project_id}/draft/review-jobs'
            body={'guidance':'Keep the core result.','standard':'Critical review','expected_draft_artifact_id':payload['source_draft_artifact_id']}
            first=client.post(url,json=body,headers=self.headers('new-review'))
            self.assertEqual(202,first.status_code,first.text)
            again=client.post(url,json=body,headers=self.headers('new-review'))
            self.assertEqual(first.json()['id'],again.json()['id'])
            conflict=client.post(url,json={**body,'standard':'Different journal'},headers=self.headers('new-review'))
            self.assertEqual(409,conflict.status_code,conflict.text)
            for changes in ({'mode':'review'},{'source_review_job_id':str(uuid.uuid4()),'issue_id':'i'}, {'expected_draft_artifact_id':''}):
                self.assertEqual(422,client.post(url,json={**body,**changes},headers=self.headers(str(uuid.uuid4()))).status_code)
            self.assertEqual(409,client.post(url,json={**body,'expected_draft_artifact_id':str(uuid.uuid4())},headers=self.headers('stale-review')).status_code)
            self.current=self.second
            denied=client.post(url,json=body,headers=self.headers('other-review'))
            self.assertIn(denied.status_code,(403,404))

    def test_retired_execution_is_rejected_before_builder(self):
        with TestClient(self.app) as client:
            payload=self.setup_payload(client);context,_=self.context(payload)
            with self.assertRaises(WorkflowValidationError):
                run_review(self.service,lambda *args:self.fail('Old paid work replayed'),context,{**payload,'flow_policy':'focused-review/1'})

    def test_false_suspicions_are_independently_dismissed_not_sent_back_as_manual_work(self):
        with TestClient(self.app) as client:
            payload=self.setup_payload(client);base,calls=self.builder(payload)
            def build(context,one):
                result=base(context,one);r=one['dialogue']['review_revision']
                if r['stage']=='propose': return {'proposal':{'patches':[],'disposition':'not_needed','explanation':'正文已有限定，原疑点不成立。'},'sources':[]}
                if r['stage']=='audit': return {**result,'checked_patch_ids':[],'no_change_needed':True,'checked_object_ids':r['group']['read_ids']}
                return result
            result=self.drive(payload,build)
            self.assertEqual('no_change_needed',result['outcome']);self.assertEqual([],result['limitations'])
            self.assertFalse(result['publication']['changed']);self.assertEqual(1,calls.count('audit'))
            self.assertNotIn('final',calls)

    def test_multi_object_group_actually_saves_body_caption_table_and_reference_together(self):
        from review_writer_core.tests.test_review_revision import TEXT,patch
        from review_writer_core.review_revision_document import document_blocks
        with TestClient(self.app) as client:
            initial=self.setup_payload(client)
            original_figure=next(b for b in initial['snapshot']['blocks'] if b['kind']=='figure')
            import re
            url=re.search(r'!\[[^\n]*\]\(([^\n]+)\)',original_figure['text']).group(1)
            text=TEXT.replace('/api/v1/artifacts/image/content',url)
            self.service.save_text(self.first,self.project_id,text=text,revision=initial['source_draft_revision'])
            current=self.service.get(self.first,self.project_id)
            payload=review_payload(self.service,self.first,self.project_id,expected_draft_artifact_id=current['draft_artifact_id'])
            blocks={b['block_id']:b for b in payload['snapshot']['blocks']}
            edits={'paragraph:abs':'The mechanism is proposed. [1]',
                'paragraph:con':'Coordination remains a proposal. [1]',
                'figure:1':blocks['figure:1']['text'].replace('Figure 1. Reproduced','Figure 1. Proposed coordination. Reproduced'),
                'table:1':blocks['table:1']['text'].replace('| A | — |','| A | CuBr₂ |'),
                'reference:1':'[1] Author. Chemical Communications. 2015. DOI: 10.1039/example'}
            source_text='Chemical Communications. 2015. DOI: 10.1039/example. CuBr₂ was used; coordination is a proposed mechanism.'
            rawblocks=[{'text':source_text,'page':1}];source_hash=digest(rawblocks)
            source={'ref':'P1:b1','paper_id':'P1','page':1,'text':source_text,'source_hash':source_hash}
            base,calls=self.builder(payload)
            def build(context,one):
                value=base(context,one);r=one['dialogue']['review_revision'];stage=r['stage']
                if stage=='prepare':
                    board={'manuscript_hash':payload['snapshot']['sha256'],'papers':{'P1':{'paper_id':'P1','title':'Fixture original','source_hash':source_hash,'available':True,'blocks':[source]}},'bindings':{}}
                    return {'board':{**board,'hash':digest(board)}}
                if stage=='plan': return {'summary':'Coordinated manuscript revision','tasks':[{'category':'accuracy','problem':'Certainty, catalyst and journal mismatches','strategy':'Correct using actual original bytes','read_ids':list(edits),'write_ids':list(edits),'paper_ids':['P1']}],'limitations':[]}
                if stage=='propose': return {'proposal':{'explanation':'同时修改受影响对象，保留论文条件和核心信息。','patches':[patch(blocks[bid],after) for bid,after in edits.items()]},'sources':[source]}
                if stage=='audit': return {**value,'checked_patch_ids':list(edits),'source_refs':['P1:b1']}
                return value
            result=self.drive(payload,build);after=self.service.get(self.first,self.project_id)
            self.assertEqual(5,result['publication']['saved_objects'])
            for bid,replacement in edits.items(): self.assertEqual(replacement,next(b['text'] for b in document_blocks(after['first_draft_md']) if b['block_id']==bid))
            self.assertIn('review_revision_verified',after['first_draft_md'])
            self.assertNotEqual(payload['source_draft_artifact_id'],after['draft_artifact_id'])
            self.assertEqual(1,calls.count('propose'));self.assertEqual(1,calls.count('audit'))

    def test_evidence_board_coverage_limits_cannot_be_hidden_by_the_planner(self):
        with TestClient(self.app) as client:
            payload=self.setup_payload(client);base,_=self.builder(payload)
            def build(context,one):
                value=base(context,one)
                if one['dialogue']['review_revision']['stage']=='prepare':
                    board=value['board'];board.pop('hash');board['limitations']=['P2 original unavailable; no scientific certification.'];board['hash']=digest(board)
                return value
            result=self.drive(payload,build)
            self.assertEqual('revised_with_limits',result['outcome'])
            self.assertIn('P2 original unavailable; no scientific certification.',result['limitations'])

    def test_text_review_does_not_resolve_unneeded_figure_files(self):
        from unittest.mock import patch
        from review_writer_core.workflow.artifacts import FIGURE_MANIFEST
        with TestClient(self.app) as client:
            payload=self.setup_payload(client)
            manifest,_=self.service._read_json(self.first,self.project_id,FIGURE_MANIFEST)
            image_ids={r['output_artifact_id'] for r in manifest['figures'] if r.get('output_artifact_id')}
            self.assertTrue(image_ids)
            original=self.service.artifacts.resolve_owned_artifact
            def resolve(owner,artifact_id,*args,**kwargs):
                self.assertNotIn(artifact_id,image_ids,'Text-only review tried to resolve a figure file')
                return original(owner,artifact_id,*args,**kwargs)
            with patch.object(self.service.artifacts,'resolve_owned_artifact',side_effect=resolve):
                fresh=review_payload(self.service,self.first,self.project_id,expected_draft_artifact_id=payload['source_draft_artifact_id'])
            self.assertEqual({},fresh['figure_artifact_paths'])

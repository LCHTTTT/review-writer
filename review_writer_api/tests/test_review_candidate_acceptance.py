"""Author can adopt an exact visible reviewer suggestion, never an issue description."""
import uuid
from fastapi.testclient import TestClient
from review_writer_api.tests.figure_test_support import NativeFigureApiTestCase
from review_writer_api.tests import test_drafts_v1 as draft_support
from review_writer_api.database import database_session
from review_writer_api.domain_services.draft_review import review_payload
from review_writer_api.errors import WorkflowConflict
from review_writer_api.workflow_models import WorkflowJob
from review_writer_core.review_revision import POLICY, validate_group
from review_writer_core.review_revision_document import digest, document_blocks
from review_writer_core.chemical_typography import normalize_chemical_typography
from review_writer_core.review_revision_public import review_result


class ReviewCandidateAcceptanceTests(NativeFigureApiTestCase):
    prepare_draft=draft_support.DraftsV1Tests.prepare_draft

    def extra_native_workflow_overrides(self):
        return draft_support.DraftsV1Tests.extra_native_workflow_overrides(self)

    def setUp(self):
        self.offline_text_provider_key='offline-fixture-not-a-real-key'
        self.noop_rewrite=False;self.hard_gate_failures=[];self.accept_rewrite_model_calls=0
        self.rewrite_source_evidence_refs=[];self.rewrite_source_check_entry={}
        self.rewrite_unsupported_claims_before=[];self.rewrite_unsupported_claims_after=[]
        super().setUp()

    def test_exact_human_acceptance_saves_new_version_without_ai_certification_or_model_job(self):
        with TestClient(self.app) as client:
            self.prepare_draft(client)
            service=self.app.state.drafts_service
            before=service.get(self.first,self.project_id)
            payload=review_payload(service,self.first,self.project_id,
                expected_draft_artifact_id=before['draft_artifact_id'])
            block=next(b for b in payload['snapshot']['blocks'] if b['kind']=='paragraph')
            task={'group_id':'revision-1','category':'consistency','problem':'AI concern (not certified)',
                'strategy':'Qualify the existing statement','read_ids':[block['block_id']],
                'write_ids':[block['block_id']],'paper_ids':[]}
            candidate=validate_group({'explanation':'A tentative wording for the author to decide on',
                'patches':[{'block_id':block['block_id'],'before_sha256':block['sha256'],
                    'after':block['text']+' The scope remains provisional.',
                    'basis':'manuscript','source_refs':[],'support_ids':[block['block_id']],
                    'operation':'qualify'}]},task,payload['snapshot']['text'],payload['snapshot']['blocks'],[])
            other=next(b for b in payload['snapshot']['blocks'] if b['kind']=='paragraph' and b['block_id']!=block['block_id'])
            def next_candidate(target,group_id,after):
                t={**task,'group_id':group_id,'write_ids':[target['block_id']],
                   'read_ids':[target['block_id']]}
                c=validate_group({'explanation':'A distinct, explicitly chosen edit',
                    'patches':[{'block_id':target['block_id'],'before_sha256':target['sha256'],
                    'after':after,'basis':'manuscript','source_refs':[],
                    'support_ids':[target['block_id']],'operation':'qualify'}]},
                    t,payload['snapshot']['text'],payload['snapshot']['blocks'],[])
                return t,c
            distinct,distinct_candidate=next_candidate(other,'revision-3',other['text']+' An additional limited observation.')
            distinct_candidate={**distinct_candidate,'sources':[],'unverified_tentative':True,
                'patches':[{**p,'basis':'source','source_refs':['unknown:b1']} for p in distinct_candidate['patches']]}
            overlapping,overlap_candidate=next_candidate(block,'revision-4',block['text']+' A conflicting alternative.')
            job_id=str(uuid.uuid4())
            board={'papers':{},'bindings':{},'manuscript_hash':payload['snapshot']['sha256'],'limitations':[]}
            board['hash']=digest(board)
            result={'policy':POLICY,'phase':'completed','outcome':'limited','board':board,
                'plan':{'summary':'Unverified suggestions','tasks':[task,{**task,'group_id':'revision-2',
                    'problem':'No returned replacement'},distinct,overlapping],'limitations':[]},
                'groups':{'revision-1':{'status':'limited','attempts':[{'candidate':candidate}]},
                          'revision-2':{'status':'limited','attempts':[],'reason':'AI supplied no replacement'},
                          'revision-3':{'status':'limited','candidate':distinct_candidate},
                          'revision-4':{'status':'limited','candidate':overlap_candidate}}, 
                'publication':{'changed':False}}
            with database_session(self.sessions) as session:
                session.add(WorkflowJob(id=uuid.UUID(job_id),user_id=uuid.UUID(self.first.user_id),
                    project_id=uuid.UUID(self.project_id),scope='project',job_type='draft.optimize',queue_name='scientific',
                    status='succeeded',idempotency_key='manual-review-candidate',
                    idempotency_scope_key='manual-review-candidate',payload_json=payload,result_json=result,
                    progress_current=2,progress_total=2))
            shown=review_result(result,payload)
            self.assertEqual(4,len(shown['revisions']))
            self.assertEqual(digest(candidate),shown['revisions'][0]['candidate_sha256'])
            self.assertEqual('no_candidate',shown['revisions'][1]['status'])
            url=f'/api/v1/projects/{self.project_id}/draft/review-candidates/{job_id}/revision-1/accept'
            self.assertEqual(409,client.post(url,json={'expected_draft_artifact_id':before['draft_artifact_id'],
                'candidate_sha256':'f'*64}).status_code)
            missing=client.post(url.replace('revision-1','revision-2'),json={
                'expected_draft_artifact_id':before['draft_artifact_id'],'candidate_sha256':digest(candidate)})
            self.assertEqual(409,missing.status_code,missing.text)
            self.current=self.second
            self.assertIn(client.post(url,json={'expected_draft_artifact_id':before['draft_artifact_id'],
                'candidate_sha256':digest(candidate)}).status_code,(403,404,409))
            self.current=self.first
            saved=client.post(url,json={'expected_draft_artifact_id':before['draft_artifact_id'],
                'candidate_sha256':digest(candidate)})
            self.assertEqual(200,saved.status_code,saved.text)
            self.assertEqual('human_approved_not_ai_certified',saved.json()['verification'])
            after=service.get(self.first,self.project_id)
            self.assertNotEqual(before['draft_artifact_id'],after['draft_artifact_id'])
            self.assertTrue(after['peer_review_job']['current'])
            self.assertEqual(2,after['peer_review_job']['pending_group_count'])
            self.assertIn('The scope remains provisional.',after['first_draft_md'])
            self.assertEqual(before['first_draft_md'],service.artifacts.resolve_owned_artifact(
                self.first.user_id,before['draft_artifact_id']).path.read_text(encoding='utf-8'))
            meta=service.artifacts.resolve_owned_artifact(self.first.user_id,after['draft_artifact_id']).artifact.metadata
            self.assertTrue(meta['author_approval_is_not_ai_verification'])
            self.assertEqual(job_id,meta['author_approved_from_review_job_id'])
            self.assertIn(block['block_id'].removeprefix('paragraph:'),meta['unverified_manual_paragraph_ids'])
            self.assertFalse((service.repository.get_job(self.first.user_id,job_id).result or {})['publication']['changed'])
            self.assertEqual(409,client.post(url,json={'expected_draft_artifact_id':before['draft_artifact_id'],
                'candidate_sha256':digest(candidate)}).status_code)
            overlap_url=url.replace('revision-1','revision-4')
            self.assertEqual(409,client.post(overlap_url,json={'expected_draft_artifact_id':after['draft_artifact_id'],
                'candidate_sha256':digest(overlap_candidate)}).status_code)
            next_url=url.replace('revision-1','revision-3')
            second=client.post(next_url,json={'expected_draft_artifact_id':after['draft_artifact_id'],
                'candidate_sha256':digest(distinct_candidate)})
            self.assertEqual(200,second.status_code,second.text)
            final=service.get(self.first,self.project_id)
            self.assertIn('The scope remains provisional.',final['first_draft_md'])
            self.assertIn('An additional limited observation.',final['first_draft_md'])
            self.assertEqual(['revision-1','revision-3'],final['author_approved_review_group_ids'])
            self.assertEqual(job_id,final['author_approved_review_job_id'])
            reject_url=overlap_url.replace('/accept','/reject')
            rejected=client.post(reject_url,json={'expected_draft_artifact_id':final['draft_artifact_id'],
                'candidate_sha256':digest(overlap_candidate)})
            self.assertEqual(200,rejected.status_code,rejected.text)
            after_reject=service.get(self.first,self.project_id)
            self.assertEqual(final['first_draft_md'],after_reject['first_draft_md'])
            self.assertEqual(['revision-4'],after_reject['peer_review_job']['rejected_group_ids'])
            self.assertEqual(0,after_reject['peer_review_job']['pending_group_count'])
            self.assertEqual(409,client.post(overlap_url,json={'expected_draft_artifact_id':after_reject['draft_artifact_id'],
                'candidate_sha256':digest(overlap_candidate)}).status_code)
            self.assertEqual(200,client.post(reject_url,json={'expected_draft_artifact_id':after_reject['draft_artifact_id'],
                'candidate_sha256':digest(overlap_candidate)}).status_code)

    def test_old_normalized_snapshot_accepts_only_the_selected_raw_object(self):
        with TestClient(self.app) as client:
            self.prepare_draft(client)
            service=self.app.state.drafts_service
            initial=service.get(self.first,self.project_id)
            raw,_=service._read_text(self.first,self.project_id,'draft/manuscript.md')
            paragraphs=[b for b in document_blocks(raw) if b['kind']=='paragraph'][:2]
            self.assertEqual(2,len(paragraphs))
            for block in reversed(paragraphs):
                raw=raw[:block['end']]+' CuBr2 remains an unverified label.'+raw[block['end']:]
            service.save_text(self.first,self.project_id,text=raw,revision=initial['revision'],operation='fixture-raw-edit')
            before=service.get(self.first,self.project_id)
            raw,_=service._read_text(self.first,self.project_id,'draft/manuscript.md')
            displayed=normalize_chemical_typography(raw)
            self.assertNotEqual(raw,displayed)
            payload=review_payload(service,self.first,self.project_id,
                expected_draft_artifact_id=before['draft_artifact_id'])
            self.assertEqual(raw,payload['snapshot']['text'])  # New reviews freeze exact artifact bytes.
            # Simulate an existing r234 job: the review saw normalized display text.
            payload['snapshot']={'text':displayed,'sha256':digest(displayed),'blocks':document_blocks(displayed)}
            target=next(b for b in payload['snapshot']['blocks'] if b['block_id']==paragraphs[0]['block_id'])
            untouched=next(b for b in document_blocks(raw) if b['block_id']==paragraphs[1]['block_id'])
            task={'group_id':'revision-1','category':'consistency','problem':'Review a provisional statement',
                'strategy':'Limit claim','read_ids':[target['block_id']],
                'write_ids':[target['block_id']],'paper_ids':[]}
            candidate=validate_group({'explanation':'Author chooses exact wording','patches':[
                {'block_id':target['block_id'],'before_sha256':target['sha256'],
                 'after':target['text']+' This finding is provisional.',
                 'basis':'manuscript','source_refs':[],'support_ids':[target['block_id']],
                 'operation':'qualify'}]},task,displayed,payload['snapshot']['blocks'],[])
            board={'papers':{},'bindings':{},'manuscript_hash':payload['snapshot']['sha256'],'limitations':[]}
            board['hash']=digest(board)
            job_id=str(uuid.uuid4())
            result={'policy':POLICY,'phase':'completed','board':board,
                'plan':{'tasks':[task]},'groups':{'revision-1':{'status':'recommended','candidate':candidate}}}
            with database_session(self.sessions) as session:
                session.add(WorkflowJob(id=uuid.UUID(job_id),user_id=uuid.UUID(self.first.user_id),
                    project_id=uuid.UUID(self.project_id),scope='project',job_type='draft.optimize',queue_name='scientific',
                    status='succeeded',idempotency_key='review-normalized-fixture',
                    idempotency_scope_key='review-normalized-fixture',payload_json=payload,result_json=result,
                    progress_current=1,progress_total=1))
            actual=next(b['text'] for b in document_blocks(raw) if b['block_id']==target['block_id'])
            displayed_before=service.get(self.first,self.project_id)['peer_review_job']['result']['revisions'][0]['patches'][0]['before']
            self.assertEqual(actual,displayed_before)
            url=f'/api/v1/projects/{self.project_id}/draft/review-candidates/{job_id}/revision-1/accept'
            response=client.post(url,json={'expected_draft_artifact_id':before['draft_artifact_id'],
                'candidate_sha256':digest(candidate)})
            self.assertEqual(200,response.status_code,response.text)
            saved,_=service._read_text(self.first,self.project_id,'draft/manuscript.md')
            self.assertEqual(untouched['text'],next(b['text'] for b in document_blocks(saved)
                if b['block_id']==untouched['block_id']))
            self.assertIn('This finding is provisional.',saved)
            old=service.artifacts.resolve_owned_artifact(self.first.user_id,before['draft_artifact_id']).path
            self.assertEqual(raw,old.read_text(encoding='utf-8'))

    def test_batch_acceptance_saves_two_groups_as_one_new_version(self):
        with TestClient(self.app) as client:
            self.prepare_draft(client)
            service=self.app.state.drafts_service
            before=service.get(self.first,self.project_id)
            payload=review_payload(service,self.first,self.project_id,
                expected_draft_artifact_id=before['draft_artifact_id'])
            blocks=[b for b in payload['snapshot']['blocks'] if b['kind']=='paragraph'][:2]
            self.assertEqual(2,len(blocks))
            tasks=[];groups={};hashes={}
            for n,block in enumerate(blocks,1):
                group_id=f'revision-{n}'
                task={'group_id':group_id,'category':'consistency','problem':f'Bounded concern {n}',
                    'strategy':'Qualify the existing statement','read_ids':[block['block_id']],
                    'write_ids':[block['block_id']],'paper_ids':[]}
                candidate=validate_group({'explanation':f'Candidate {n}',
                    'patches':[{'block_id':block['block_id'],'before_sha256':block['sha256'],
                        'after':block['text']+f' Qualified statement {n}.','basis':'manuscript',
                        'source_refs':[],'support_ids':[block['block_id']],'operation':'qualify'}]},
                    task,payload['snapshot']['text'],payload['snapshot']['blocks'],[])
                tasks.append(task);groups[group_id]={'status':'recommended','candidate':candidate}
                hashes[group_id]=digest(candidate)
            job_id=str(uuid.uuid4())
            board={'papers':{},'bindings':{},'manuscript_hash':payload['snapshot']['sha256'],'limitations':[]}
            board['hash']=digest(board)
            from review_writer_api.domain_services.review_boards import write_board
            board_ref=write_board(service,self.first.user_id,board)
            result={'policy':POLICY,'phase':'completed','board_ref':board_ref,
                'plan':{'summary':'Two bounded suggestions','tasks':tasks,'limitations':[]},
                'groups':groups,'publication':{'changed':False}}
            with database_session(self.sessions) as session:
                session.add(WorkflowJob(id=uuid.UUID(job_id),user_id=uuid.UUID(self.first.user_id),
                    project_id=uuid.UUID(self.project_id),scope='project',job_type='draft.optimize',queue_name='scientific',
                    status='succeeded',idempotency_key='batch-review-candidate',
                    idempotency_scope_key='batch-review-candidate',payload_json=payload,result_json=result,
                    progress_current=2,progress_total=2))
            from unittest.mock import patch
            with patch.object(service,'validate_artifact_inputs',side_effect=AssertionError('blanket upstream check')):
                saved=client.post(f'/api/v1/projects/{self.project_id}/draft/review-candidates/{job_id}/accept-selected',
                    json={'expected_draft_artifact_id':before['draft_artifact_id'],'groups':[
                        {'group_id':'revision-1','candidate_sha256':hashes['revision-1']},
                        {'group_id':'revision-2','candidate_sha256':hashes['revision-2']}]})
            self.assertEqual(200,saved.status_code,saved.text)
            self.assertEqual(2,saved.json()['groups_saved'])
            after=service.get(self.first,self.project_id)
            for n in (1,2):
                self.assertIn(f'Qualified statement {n}.',after['first_draft_md'])
            self.assertEqual(['revision-1','revision-2'],after['author_approved_review_group_ids'])
            self.assertEqual(0,after['peer_review_job']['pending_group_count'])
            self.assertEqual(len(before['versions'])+1,len(after['versions']))

    def test_cancelled_new_review_keeps_its_generated_candidate_acceptable(self):
        with TestClient(self.app) as client:
            self.prepare_draft(client)
            service=self.app.state.drafts_service
            before=service.get(self.first,self.project_id)
            payload=review_payload(service,self.first,self.project_id,
                expected_draft_artifact_id=before['draft_artifact_id'])
            self.assertTrue(payload['author_selects'])
            block=next(b for b in payload['snapshot']['blocks'] if b['kind']=='paragraph')
            task={'group_id':'revision-1','category':'consistency','problem':'A bounded concern',
                'strategy':'Qualify the existing statement','read_ids':[block['block_id']],
                'write_ids':[block['block_id']],'paper_ids':[]}
            candidate=validate_group({'explanation':'A candidate the author can keep after stopping',
                'patches':[{'block_id':block['block_id'],'before_sha256':block['sha256'],
                    'after':block['text']+' The claim remains limited.',
                    'basis':'manuscript','source_refs':[],'support_ids':[block['block_id']],
                    'operation':'qualify'}]},task,payload['snapshot']['text'],payload['snapshot']['blocks'],[])
            job_id=str(uuid.uuid4())
            board={'papers':{},'bindings':{},'manuscript_hash':payload['snapshot']['sha256'],'limitations':[]}
            board['hash']=digest(board)
            result={'policy':POLICY,'phase':'propose','outcome':'awaiting_author_choice','board':board,
                'plan':{'summary':'One suggestion','tasks':[task],'limitations':[]},
                'groups':{'revision-1':{'status':'recommended','candidate':candidate}},
                'publication':{'changed':False}}
            with database_session(self.sessions) as session:
                session.add(WorkflowJob(id=uuid.UUID(job_id),user_id=uuid.UUID(self.first.user_id),
                    project_id=uuid.UUID(self.project_id),scope='project',job_type='draft.optimize',queue_name='scientific',
                    status='cancelled',idempotency_key='cancelled-review-candidate',
                    idempotency_scope_key='cancelled-review-candidate',payload_json=payload,result_json=result,
                    progress_current=1,progress_total=1))
            selected=service.get(self.first,self.project_id,review_job_id=job_id)
            self.assertEqual(job_id,selected['peer_review_job']['id'])
            self.assertEqual(1,selected['peer_review_job']['pending_group_count'])
            self.assertTrue(any(run['id']==job_id for run in selected['review_runs']))
            routed=client.get(f'/api/v1/projects/{self.project_id}/draft?review_job_id={job_id}')
            self.assertEqual(200,routed.status_code,routed.text)
            self.assertEqual(job_id,routed.json()['peer_review_job']['id'])
            saved=client.post(f'/api/v1/projects/{self.project_id}/draft/review-candidates/{job_id}/revision-1/accept',
                json={'expected_draft_artifact_id':before['draft_artifact_id'],'candidate_sha256':digest(candidate)})
            self.assertEqual(200,saved.status_code,saved.text)
            self.assertEqual('human_approved_not_ai_certified',saved.json()['verification'])
            self.assertIn('The claim remains limited.',service.get(self.first,self.project_id)['first_draft_md'])

    def test_referenced_source_file_change_blocks_acceptance(self):
        import hashlib
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from review_writer_api.domain_services.review_candidate_acceptance import _verify_source_files

        with tempfile.TemporaryDirectory() as root_name:
            root=Path(root_name)
            source=root/'source.md'
            source.write_text('Original source',encoding='utf-8')
            expected=hashlib.sha256(source.read_bytes()).hexdigest()
            record=SimpleNamespace(metadata={'source_paths':{'content_list':str(source)}},
                markdown_relative_path='',pdf_relative_path='')
            library=SimpleNamespace(get=lambda principal,paper_id: record)
            def sha256(path):
                return hashlib.sha256(Path(path).read_bytes()).hexdigest()
            service=SimpleNamespace(artifacts=SimpleNamespace(
                workspace_manager=SimpleNamespace(user_root=lambda user_id: root),_sha256=sha256))
            board={'papers':{'P1':{'source_kind':'mineru_content_list','source_sha256':expected}}}
            candidate={'patches':[{'basis':'source','source_refs':['P1:b1']}],
                'sources':[{'ref':'P1:b1','paper_id':'P1','text':'Original source'}]}
            principal=SimpleNamespace(user_id='u')
            with patch('review_writer_api.domain_services.review_pdf_checks._library',return_value=library):
                _verify_source_files(service,principal,board,candidate)
                source.write_text('Changed source',encoding='utf-8')
                with self.assertRaises(WorkflowConflict):
                    _verify_source_files(service,principal,board,candidate)

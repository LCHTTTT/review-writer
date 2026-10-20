"""Two-slot durable review calls. No threads, shared workspaces or paid replay."""
from review_writer_api.errors import WorkflowValidationError, WorkflowConflict
from review_writer_api.job_service import JobYieldRequested
from review_writer_api.scientific_runner import ScientificModelDeferred, ScientificRunError
from review_writer_core.model_gateway_client import parse_json_object_text
from review_writer_core.review_revision_document import digest
from review_writer_core.review_revision_schedule import REVIEW_CONCURRENCY, scopes_conflict


class ReviewPending(Exception):
    pass


class ReviewUnitFailed(WorkflowValidationError):
    pass


class ReviewCalls:
    def __init__(self, context, state, payload, builder, checkpoint, board=None):
        self.context=context;self.state=state;self.payload=payload
        self.builder=builder;self.checkpoint=checkpoint
        self.board=board if board is not None else state.get('board')
        self.records=state.setdefault('model_calls',{})

    def child(self, record):
        child=self.context.repository.get_job(self.context.user_id,record['job_id'])
        if (child is None or child.project_id!=self.context.project_id or child.job_type!='model.dispatch'
                or child.payload.get('parent_job_id')!=record['parent_job_id']):
            raise WorkflowConflict('审稿模型子任务归属不匹配，未重发请求。')
        return child

    def pending(self):
        return {key:value for key,value in self.records.items() if value.get('status')=='waiting'}

    def invoke(self, stage, *, call_key=None, **extra):
        key=call_key or stage
        fingerprint=digest({'stage':stage,'extra':extra,'snapshot':self.payload['snapshot']['sha256'],
                            'board':(self.board or {}).get('hash'),
                            'pdf_checks':self.payload.get('confirmed_pdf_checks',[])})
        record=self.records.get(key)
        if record and record['fingerprint']!=fingerprint:
            raise WorkflowConflict('审稿调用输入与检查点不一致，未创建新模型请求。')
        response={}
        if record:
            if record.get('status')=='submitting':
                record.update(status='failed',error=f'{key}：模型提交结果未确认，未自动重发；请核对原模型任务。')
                self.checkpoint(stage)
            if record.get('status')=='failed':
                raise ReviewUnitFailed(record['error'])
            if record.get('status')=='done':
                return record['value']
            child=self.child(record)
            if child.status in ('queued','running'):
                raise ReviewPending()
            if child.status!='succeeded':
                message=f'{key}：'+(child.error_message or '模型未完成；原请求未重发。')
                record.update(status='failed',error=message,error_code=child.error_code)
                self.checkpoint(stage)
                raise ReviewUnitFailed(message)
            try:
                response={'model_response':parse_json_object_text(str((child.result or {}).get('output_text') or ''))}
            except (ValueError,RuntimeError) as exc:
                record.update(status='failed',error=f'{key}：模型答案格式无效，未自动重发。')
                self.checkpoint(stage)
                raise ReviewUnitFailed(record['error']) from exc
        else:
            waiting=self.pending()
            scope=extra.get('task')
            if len(waiting)>=REVIEW_CONCURRENCY or (scope and any(
                    value.get('scope') and scopes_conflict(scope,value['scope']) for value in waiting.values())):
                raise ReviewPending()
        if record is None:
            # Persist intent before the subprocess can submit anything. A crash
            # before the child ID is checkpointed is uncertain, never permission
            # to create another paid request under a retry parent's new ID.
            self.records[key]={'status':'submitting','fingerprint':fingerprint,'parent_job_id':self.context.job_id}
        self.checkpoint(stage)
        request={'stage':stage,'snapshot':self.payload['snapshot'],'board':self.board,
                 'confirmed_pdf_checks':self.payload.get('confirmed_pdf_checks',[]),
                 'suggest_pdf_quote':bool(self.payload.get('recommend_only')),
                 'suggest_paper_id':self.payload.get('suggest_paper_id',''),
                 'standard':self.payload.get('standard',''),'guidance':self.payload.get('guidance',''),**extra,**response}
        one={**self.payload,'revision_mode':'dialogue','dialogue':{'review_revision':request}}
        try:
            value=self.builder(self.context,one)
        except ScientificModelDeferred as exc:
            if len(exc.model_job_ids)!=1:
                raise WorkflowValidationError('审稿单元返回多个模型任务，未扩大并发。') from exc
            self.records[key]={'status':'waiting','job_id':exc.model_job_id,'parent_job_id':self.context.job_id,
                               'fingerprint':fingerprint,'scope':extra.get('task')}
            self.checkpoint(stage)
            raise ReviewPending() from exc
        except ScientificRunError as exc:
            self.records[key]={'status':'failed','fingerprint':fingerprint,'error':f'{key}：{exc}'}
            self.checkpoint(stage)
            raise ReviewUnitFailed(self.records[key]['error']) from exc
        if not isinstance(value,dict):
            raise ReviewUnitFailed(f'{key}：修订阶段未返回有效结果，检查点保留，未覆盖正文。')
        self.records[key]={'status':'done','fingerprint':fingerprint,'value':value}
        return value

    def wait(self):
        self.checkpoint(self.state.get('phase','plan'))
        raise JobYieldRequested(delay_seconds=10,queue_reason='model_waiting')

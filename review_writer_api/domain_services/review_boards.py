"""Content-addressed review evidence boards, kept outside job.result.

A review board contains every original-source block used by the planner and
probe stages. Writing it into every job checkpoint multiplied a large immutable
object by the number of units/groups and made Draft polling load it repeatedly.
The board is now stored once per user/hash; jobs keep only `{version, hash}`.
"""
import json

from review_writer_api.errors import WorkflowConflict, WorkflowValidationError
from review_writer_core.atomic_io import atomic_write_json
from review_writer_core.review_revision_document import digest


def _board_root(service, user_id):
    return service.artifacts.workspace_manager.trusted_user_directory(
        user_id, ".review-writer", "review-boards")


def _board_path(service, user_id, board_hash):
    value=str(board_hash or '')
    if len(value)!=64 or any(char not in '0123456789abcdef' for char in value):
        raise WorkflowValidationError('审稿证据板标识无效。')
    return _board_root(service,user_id) / f"{value}.json"


def write_board(service, user_id, board):
    if not isinstance(board,dict) or not isinstance(board.get('hash'),str):
        raise WorkflowValidationError('审稿证据板格式无效。')
    if digest({key:value for key,value in board.items() if key!='hash'})!=board['hash']:
        raise WorkflowValidationError('审稿证据板指纹不一致。')
    path=_board_path(service,user_id,board['hash'])
    if not path.is_file():
        atomic_write_json(path,board)
    return {'version':1,'hash':board['hash']}


def read_board(service, user_id, ref):
    if not isinstance(ref,dict) or not ref.get('hash'):
        raise WorkflowValidationError('审稿证据板引用无效。')
    path=_board_path(service,user_id,ref['hash'])
    if not path.is_file():
        raise WorkflowConflict('审稿证据板不可用，请重新审稿。')
    try:
        board=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as exc:
        raise WorkflowConflict('审稿证据板无法读取，请重新审稿。') from exc
    if (not isinstance(board,dict) or board.get('hash')!=ref['hash']
            or digest({key:value for key,value in board.items() if key!='hash'})!=board['hash']):
        raise WorkflowConflict('审稿证据板校验失败，请重新审稿。')
    return board


def load_job_board(service, user_id, project_id, job):
    """Load a new `board_ref` or a frozen legacy `board` from a job result."""
    result=(job.result or {}) if job else {}
    if result.get('board_ref'):
        return read_board(service,user_id,result['board_ref'])
    board=result.get('board')
    if isinstance(board,dict):
        return board
    raise WorkflowConflict('审稿证据板不可用，请重新审稿。')

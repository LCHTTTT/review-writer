"""Recover review evidence from current original bytes, never from model prose."""
from copy import deepcopy
import hashlib
import json
import re


def bibliography_context(markdown):
    match = re.search(r'(?im)^#{1,6}\s+(?:references|bibliography|参考文献)\s*$', markdown)
    return markdown[match.start():] if match else ''


def contextual_review_evidence(evidence, hints, load, *, bibliography=False):
    """Re-resolve exact saved source excerpts and include attribution context.

    `load` must resolve only papers bound to the current project's Matrix/library.
    Whole original blocks are returned, without fuzzy chemical matching or clipping.
    """
    result = deepcopy(evidence)
    papers = {p['paper_id']:p for p in result.get('evidence', [])}
    by_paper = {}
    for hint in hints:
        if isinstance(hint, dict) and hint.get('paper_id') and hint.get('text'):
            by_paper.setdefault(hint['paper_id'], []).append(hint['text'])
    bibliographic_ids = set(papers) if bibliography is True else set(bibliography or [])
    for pid in dict.fromkeys([*papers, *by_paper, *sorted(bibliographic_ids)]):
        doc = load(pid)
        blocks = doc.get('blocks') or []
        if not blocks:
            continue
        normalize = lambda text: ' '.join(str(text).split())
        selected = set()
        texts = [normalize(b.get('text', '')) for b in blocks]
        for hint in by_paper.get(pid, []):
            exact = normalize(hint)
            matches = [i for i, text in enumerate(texts) if exact and exact in text]
            if len(matches) == 1:
                selected.add(matches[0])
        # For paper metadata questions, show actual first-page blocks, not a
        # journal name guessed from the DOI prefix. Bound complete blocks only.
        if pid in bibliographic_ids:
            selected.update(i for i,b in enumerate(blocks) if b.get('page') in (1, '1'))
        existing = papers.get(pid, {}).get('original_passages', [])
        context_texts = [p.get('text', '') for p in existing] + [blocks[i].get('text', '') for i in selected]
        for passage in context_texts:
            if not re.search(r'our report|earlier|previous|ref\.?\s*\d', passage, re.I):
                continue
            normalized = normalize(passage)
            matches = [i for i, text in enumerate(texts) if normalized and normalized in text]
            for index in matches:
                selected.update(range(max(0,index-2),min(len(blocks),index+2)))
            refs = re.findall(r'ref\.?\s*(\d+)[a-z]?', passage, re.I)
            for number in refs:
                selected.update(i for i,b in enumerate(blocks) if re.search(r'^\s*'+re.escape(number)+r'\s+(?:\(|[A-Z])', b.get('text','')))
        if not selected and pid not in papers:
            continue
        paper = papers.setdefault(pid, {'paper_id':pid,'original_passages':[], 'source_path':doc.get('source_path','')})
        digest = hashlib.sha256(json.dumps(blocks,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if paper.get('source_content_hash') and paper['source_content_hash'] != digest:
            raise ValueError('原文版本在审稿证据恢复期间发生变化，未混用不同版本。')
        paper['source_content_hash'] = digest
        paper['local_source_available'] = True
        paper['original_text_available'] = True
        for index in sorted(selected):
            block = blocks[index]
            if not block.get('text'):
                continue
            ref = f"{pid}:p{block.get('page') or 'unknown'}:b{index+1}"
            span = {'ref':ref,'page':block.get('page'),'text':block['text'],'context_origin':'current_original_block'}
            # Never give the same ref to different text.
            if any(p.get('ref') == ref and p.get('text') != span['text'] for p in paper['original_passages']):
                span['ref'] = ref+':'+hashlib.sha256(span['text'].encode()).hexdigest()[:12]
            if not any(p.get('text') == span['text'] for p in paper['original_passages']):
                paper['original_passages'].append(span)
    result['evidence'] = list(papers.values())
    result['paper_ids'] = list(papers)
    result['original_source_ready'] = any(p.get('original_passages') for p in papers.values())
    result['local_source_available'] = any(p.get('local_source_available') for p in papers.values())
    return result

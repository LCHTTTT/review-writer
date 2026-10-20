"""Exact, versioned editing objects for review-style manuscript revision."""
import hashlib
import json
import re
from collections import Counter
from review_writer_core.paragraph_markers import parse_marked_paragraphs, REFERENCES_RE, PARAGRAPH_MARKER_RE


def digest(value):
    data = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def document_blocks(markdown):
    blocks = []
    def add(key, kind, start, end):
        if kind=='figure': key=key.rstrip('.')
        if any(start < b['end'] and end > b['start'] for b in blocks):
            return
        text = markdown[start:end]
        blocks.append({'block_id':key,'kind':kind,'start':start,'end':end,'text':text,'sha256':digest(text)})
    for p in parse_marked_paragraphs(markdown):
        add('paragraph:'+p['paragraph_id'], 'paragraph', p['start'], p['end'])
    for match in re.finditer(r'(?m)^!\[[^\n]*\]\([^\n]+\)\r?\n\*(?:Figure|Scheme|图)\s+([\w.-]+)[^\n]*',markdown,re.I):
        add('figure:'+match.group(1),'figure',match.start(),match.end())
    # Blank-line blocks avoid interpreting Markdown inside prose as new objects.
    for match in re.finditer(r'(?ms)(?:\A|(?<=\n\n))[^\n].*?(?=\n\n|\Z)', markdown):
        start, end = match.span()
        raw = match.group().rstrip('\r\n'); end = start+len(raw)
        if raw.startswith(('<!--','```','~~~')):
            continue
        if re.match(r'^!\[[^\n]*\]\([^\n]+\)\s*\n\*(?:Figure|Scheme|图)\s+',raw,re.I):
            number = re.search(r'\n\*(?:Figure|Scheme|图)\s+([\w.-]+)',raw,re.I).group(1)
            add('figure:'+number,'figure',start,end)
        elif raw.lstrip().startswith('|') and re.search(r'(?m)^\s*\|?\s*:?-{3}',raw):
            add('table:'+str(sum(b['kind']=='table' for b in blocks)+1),'table',start,end)
        elif re.match(r'^(?:Table|表)\s+[\w.-]+[.: ]',raw,re.I):
            add('table-caption:'+str(sum(b['kind']=='table-caption' for b in blocks)+1),'table-caption',start,end)
    reference = REFERENCES_RE.search(markdown)
    if reference:
        for match in re.finditer(r'(?m)^\[(\d+)\][^\n]*(?:\n(?!\[\d+\]|#|\s*$)[^\n]+)*',markdown[reference.end():]):
            add('reference:'+match.group(1),'reference',reference.end()+match.start(),reference.end()+match.end())
    for n,match in enumerate(re.finditer(r'(?m)^#{1,6}\s+[^\n]+',markdown)):
        if REFERENCES_RE.fullmatch(match.group()):
            continue
        add('heading:'+str(n),'heading',match.start(),match.end())
    return sorted(blocks,key=lambda b:b['start'])


def plan_object_aliases(markdown, blocks):
    """Resolve only unambiguous generated-table markers to their exact object.

    This is not a fuzzy paragraph lookup or permission to edit the caption,
    provenance or legend. Those remain outside the table's writable span.
    """
    markers=Counter(PARAGRAPH_MARKER_RE.findall(markdown))
    ids={b['block_id'] for b in blocks}; aliases={}
    legends={
        '— = unavailable in the extracted records; this does not establish absence from the original paper.',
        'Numbered entries within a row refer to the same substrate/example across columns; results are not combined best-case values.',
    }
    for block in blocks:
        if block['kind']!='table' or markdown[block['start']:block['end']]!=block['text']:
            continue
        match=re.match(r'\s*([^<]*?)<!--\s*reaction_table:\s*(\{[^\n]*\})\s*-->\s*'
                       r'<!--\s*paragraph_id:\s*([A-Za-z0-9_.:-]+)\s*-->',markdown[block['end']:],re.S)
        if not match or any(line.strip() not in legends for line in match[1].splitlines() if line.strip()):
            continue
        try:
            metadata=json.loads(match[2])
        except (ValueError,TypeError):
            continue
        marker=match[3]; alias='paragraph:'+marker
        section=metadata.get('section_id') if isinstance(metadata,dict) else None
        if (isinstance(section,str) and section and re.fullmatch(re.escape(section)+r'-T\d+',marker)
                and metadata.get('projection')=='section-reaction-table/3'
                and markers[marker]==1 and alias not in ids):
            aliases[alias]=block['block_id']
    return aliases


def validate_patch(block, patch, reference_numbers):
    if not isinstance(patch,dict) or patch.get('block_id') != block['block_id'] or patch.get('before_sha256') != block['sha256']:
        raise ValueError('修改位置或原文指纹不匹配。')
    text=patch.get('after')
    if not isinstance(text,str) or not text.strip() or text==block['text']:
        raise ValueError('修改组必须包含实际且非空的修改，不能删除整个对象。')
    if '<!--' in text or '-->' in text or '```' in text or '~~~' in text:
        raise ValueError('不能在修订中改写元数据、身份标记或插入代码块。')
    before=block['text']; kind=block['kind']
    if kind in ('paragraph','table-caption','reference') and (re.search(r'(?m)^\s*(?:#{1,6}\s|\|)',text) or '![' in text):
        raise ValueError('当前文字对象内不能插入章节、图像或表格对象。')
    if kind!='figure' and re.findall(r'!\[[^\n]*\]\([^\n]+\)',text)!=re.findall(r'!\[[^\n]*\]\([^\n]+\)',before):
        raise ValueError('不能新增、删除或替换图像地址。')
    if kind=='heading':
        level=re.match(r'^#+(?=\s)',text)
        if text.count('\n') or not level or level.group()!=re.match(r'^#+',before).group():
            raise ValueError('标题层级必须保留。')
    if kind=='figure':
        images=lambda s: re.findall(r'!\[[^\n]*\]\(([^\n]+)\)',s)
        labels=lambda s: re.findall(r'\*(Figure|Scheme|图)\s+([\w.-]+)',s,re.I)
        rights=lambda s: re.findall(r'(?:Reproduced|Adapted) from Ref\.\s*\d+[^*\n]*',s,re.I)
        if images(text)!=images(before) or labels(text)!=labels(before) or rights(text)!=rights(before) or len(text.splitlines())!=len(before.splitlines()):
            raise ValueError('图像地址、编号、来源与授权说明不可改写。')
    elif kind in {'table-caption','reference'}:
        pattern=r'^\[\d+\]' if kind=='reference' else r'^(?:Table|表)\s+[\w.-]+'
        if not re.match(pattern,text,re.I) or re.match(pattern,text,re.I).group()!=re.match(pattern,before,re.I).group():
            raise ValueError('引用或表格编号必须保留。')
        if kind=='reference' and re.search(r'\n\s*(?:\[\d+\]|#)',text):
            raise ValueError('不能新增参考条目或章节。')
    elif kind=='table':
        lines=text.splitlines()
        widths=[len(line.strip().strip('|').split('|')) for line in lines]
        if len(lines)<3 or not all(line.strip().startswith('|') for line in lines) or len(set(widths))!=1 or not re.match(r'^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$',lines[1]):
            raise ValueError('修改后的表格结构无效。')
    for contents in re.findall(r'(?<!!)\[([\d,\s–—-]+)\]',text):
        for number in re.findall(r'\d+',contents):
            if number not in reference_numbers and not re.search(r'\[[^\]]*\b'+re.escape(number)+r'\b[^\]]*\]',before):
                raise ValueError('修订不能引入未绑定的参考文献编号。')
    return {'block_id':block['block_id'],'before_sha256':block['sha256'],'after':text}


def apply_groups(markdown, blocks, groups, *, human_approved=False):
    indexed={b['block_id']:b for b in blocks}; changes={}
    refs={b['block_id'].split(':')[1] for b in blocks if b['kind']=='reference'}
    # A citation map can exist even when the manuscript references are not yet complete.
    match=re.search(r'<!--\s*citation_map:\s*(\{.*?\})\s*-->',markdown,re.S)
    if match:
        refs.update(json.loads(match.group(1)))
    for group in groups:
        for patch in group.get('patches',[]):
            bid=patch.get('block_id')
            if bid not in indexed or bid in changes:
                raise ValueError('修改组包含未知对象或重叠写入。')
            changes[bid]=validate_patch(indexed[bid],patch,refs)
    updated=markdown
    for bid in sorted(changes,key=lambda key:indexed[key]['start'],reverse=True):
        block=indexed[bid]
        if markdown[block['start']:block['end']]!=block['text']:
            raise ValueError('正文快照已变化。')
        updated=updated[:block['start']]+changes[bid]['after']+updated[block['end']:]
    # No invented objects or deletion of markers, images, source maps or ordering.
    if [b['block_id'] for b in document_blocks(updated)] != [b['block_id'] for b in blocks]:
        raise ValueError('修订改变了对象身份或文档结构，未保存。')
    figures={p['block_id'] for g in groups for p in g.get('patches',[]) if p['block_id'].startswith('figure:')}
    if figures:
        def figure_metadata(match):
            data=json.loads(match.group(1)); body=match.group(2)
            label=re.search(r'\n\*(?:Figure|Scheme|图)\s+([\w.-]+)',body,re.I)
            if not label or 'figure:'+label.group(1).rstrip('.') not in figures:
                return match.group()
            caption=body.rsplit('\n',1)[-1].strip()
            previous=indexed['figure:'+label.group(1).rstrip('.')]['text'].rsplit('\n',1)[-1].strip()
            description=re.sub(r'^\*(?:Figure|Scheme|图)\s+[\w.-]+[.: ]*','',caption,flags=re.I)
            description=re.sub(r'(?:Reproduced|Adapted) from Ref\.\s*\d+[^*\n]*','',description,flags=re.I).strip('* .:;')
            if caption==previous or not description:
                return match.group()
            data['caption_quality']={**data.get('caption_quality',{}),
                'human_modified':human_approved,'review_revision_verified':not human_approved}
            if not human_approved:
                data['caption_quality'].update(status='ready',warnings=[])
                data['caption_normalization_status']='cleaned'
            data['caption_provenance']={'method':'author_approved_review_candidate' if human_approved else 'review_revision',
                'fingerprint':digest(body),
                'previous':data.get('caption_provenance',{})}
            return '<!-- inserted_figure: '+json.dumps(data,ensure_ascii=False)+' -->'+body
        updated=re.sub(r'<!--\s*inserted_figure:\s*(\{[^\n]*\})\s*-->(\s*\n!\[[^\n]*\]\([^\n]+\)\r?\n\*(?:Figure|Scheme|图)\s+[^\n]*)',figure_metadata,updated,flags=re.I)
    return updated

"""依据已有文献切分节点；保留数据库正文字符位置，不联网、不补写史实。"""
import csv
import hashlib
import json
import re
from pathlib import Path
import app

# 名称为整理标签，日期仅在引文明确给出或同段明确承接时赋值。
RULES = [
    ('N01', '北上抗日先遣行动', '前期行动', r'红七军团.*?北上抗日先遣队'),
    ('N02', '红二十五军开始战略转移', '部队出发', r'红二十五军.*?第二先遣队.*?战略转移'),
    ('N03', '红一、红四方面军会师', '部队会师', r'红一、红四方面军会师'),
    ('N04', '提出在川陕甘建立革命根据地的设想', '战略决策', r'川陕甘.*?根据地.*?设想'),
    ('N05', '长征到达陕北', '到达节点', r'长征到达陕北'),
    ('N06', '红一方面军东征', '后续行动', r'红一方面军.*?东征'),
]


def split_text(text):
    """完整保留标点，以原正文左闭右开字符位置溯源；句内分句用于区分并列节点。"""
    for match in re.finditer(r'[^。！？；\n]+[。！？；]?|\n+', text):
        if match.group().strip(): yield match.start(), match.end(), match.group()


def classify(text):
    nodes = [r[0] for r in RULES if re.search(r[3], text)]
    if nodes: return '节点材料', nodes
    if '1934年10月至1936年10月' in text or '主力大会师' in text: return '长征总体过程', []
    if re.search(r'193[123]年|九一八|对日战争宣言|反日反蒋', text): return '历史背景', []
    if re.search(r'沿途|宣传|发动群众|抗日义勇军', text): return '沿途政治与群众工作', []
    if re.search(r'最终成为|强大动力|长征最终胜利', text): return '意义与后续影响', []
    return '背景及补充材料', []


def build():
    with app.db() as c:
        docs = [dict(r) for r in c.execute("SELECT * FROM documents WHERE status!='排除'")]
    out = app.DATA / '节点切分'; out.mkdir(parents=True, exist_ok=True)
    chunks, excluded, sources = [], [], []
    for doc in docs:
        text = doc['body']
        original_source = re.search(r'来源[：:]([^\n]+)', text)
        sources.append({'id': doc['id'], 'title': doc['title'], 'url': doc['url'], 'institution': doc['institution'],
                        'original_source': original_source.group(1) if original_source else '未识别',
                        'document_status': doc['status'], 'body_sha256': hashlib.sha256(text.encode()).hexdigest()})
        # 对已知文章，以正文标题为边界去除导航与网页元数据；原始正文不改写。
        start = text.find('长征的定义')
        if start < 0: start = 0
        for a, b, fragment in split_text(text):
            if a < start or re.match(r'^《\s*人民日报', fragment.strip()) or fragment.strip() in ('长征的定义', '长征与北上抗日'):
                excluded.append({'document_id': doc['id'], 'start': a, 'end': b, 'text': fragment, 'reason': '导航、元数据或章节标题'})
                continue
            category, nodes = classify(fragment)
            # 一个句段可关联多个节点，但只保存一份文本，不强行拆分因果/先后叙述。
            dates = re.findall(r'\d{4}年(?:\d{1,2}月)?(?:\d{1,2}日)?', fragment)
            chunk = {'id': f"{doc['id']}:{a}-{b}", 'document_id': doc['id'], 'source_title': doc['title'],
                     'source_url': doc['url'], 'original_source': sources[-1]['original_source'],
                     'start': a, 'end': b, 'paragraph': text[:a].count('\n')+1, 'text': fragment,
                     'category': category, 'nodes': nodes, 'dates_explicit': dates,
                     'units': [u for u in app.UNITS if u in fragment],
                     'people': [p for p in app.CONFIG['people'] if p in fragment],
                     'review_status': '待核对'}
            assert text[a:b] == fragment
            chunks.append(chunk)
    node_rows = []
    for ident, title, category, _ in RULES:
        evidence = [x['id'] for x in chunks if ident in x['nodes']]
        if evidence: node_rows.append({'id': ident, 'title': title, 'category': category, 'evidence_chunks': evidence,
                                       'status': '原文支持，节点重要性为整理判断'})
    result = {'scope': '仅基于现有非排除文献，不代表完整长征过程；节点编号是展示顺序，并非所有部队的统一时间线。',
              'sources': sources, 'nodes': node_rows, 'chunks': chunks, 'excluded': excluded}
    (out/'节点与文本.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    with (out/'文本切分.csv').open('w', encoding='utf-8-sig', newline='') as f:
        fields = list(chunks[0]) if chunks else ['id']
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for chunk in chunks:
            w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v for k,v in chunk.items()})
    lines = ['# 已有文献的长征节点与文本归类', '', result['scope'], '',
             '说明：以下正文引文保留原文；分类与节点名称为整理标签。字符位置按数据库 body 字段计数（从0开始，右端不包含）。', '',
             '现有材料中有官方机构转载的媒体文章，按用户已确认纳入状态保留，原始出处另行标注。', '']
    for s in sources:
        lines += [f"来源：[{s['title']}]({s['url']})；发布机构：{s['institution']}；原始出处：{s['original_source']}；文献编号：{s['id']}。", '']
    def append_chunks(items):
        for x in items:
            lines.extend([f"> {x['text']}", '', f"出处：[{x['source_title']}]({x['source_url']})，正文第{x['paragraph']}行，字符{x['start']}—{x['end']}；片段编号 `{x['id']}`。", ''])
    for n in node_rows:
        lines += [f"## {n['id']} {n['title']}（{n['category']}）", '']
        append_chunks([x for x in chunks if n['id'] in x['nodes']])
    for category in dict.fromkeys(x['category'] for x in chunks if not x['nodes']):
        lines += [f'## {category}', '']; append_chunks([x for x in chunks if x['category']==category and not x['nodes']])
    lines += ['## 覆盖缺口', '', '现有材料没有详细记载中央红军出发、湘江战役、遵义会议、四渡赤水、渡金沙江、泸定桥、雪山草地等节点经过，也不足以展开红二、红四方面军的完整过程；本次不补写这些节点。', '',
              '原文部分句子涉及多个阶段，采用多节点关联而非改写引文。未写明的节点日期保持未知；如“11月”承接上文年份，应经人工核对后再规范化。']
    (out/'节点归类.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'documents':len(docs), 'nodes':len(node_rows), 'chunks':len(chunks), 'output':str(out)},ensure_ascii=True))
    return result


if __name__ == '__main__': build()

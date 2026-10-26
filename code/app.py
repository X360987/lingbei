"""长征官方文献试点。标准库可运行；PDF / OCR 为可选依赖。"""
import csv
import hashlib
import io
import json
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
DATA = (ROOT / CONFIG['data_dir']).resolve()
LOCK = threading.Lock()
AGENT = 'LongMarchResearchPilot/0.1'


def db():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA / 'archive.sqlite3')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS documents(
      id TEXT PRIMARY KEY, url TEXT UNIQUE, title TEXT, institution TEXT,
      kind TEXT, status TEXT DEFAULT '待核对', published TEXT, collected TEXT,
      sha256 TEXT, raw_path TEXT, body TEXT, images TEXT, error TEXT);
    CREATE TABLE IF NOT EXISTS events(
      id INTEGER PRIMARY KEY, document_id TEXT, name TEXT, date_text TEXT,
      year INTEGER, date_precision TEXT, units TEXT, people TEXT, places TEXT,
      event_type TEXT, evidence TEXT, locator TEXT, status TEXT DEFAULT '待核对',
      notes TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, url TEXT, state TEXT, message TEXT, created TEXT);
    ''')
    return conn


class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.links = []; self.images = []; self.skip = 0; self.title = ''; self.in_title = False; self.published = ''

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('script', 'style'): self.skip += 1
        if tag == 'title': self.in_title = True
        if tag in ('p', 'div', 'br', 'h1', 'li'): self.parts.append('\n')
        if tag == 'a' and a.get('href'): self.links.append((a['href'], a.get('title', '')))
        if tag == 'img' and a.get('src'): self.images.append({'url': a['src'], 'caption': a.get('alt') or a.get('title', '')})
        if tag == 'meta' and (a.get('name', '').lower() in ('pubdate', 'publishdate', 'publishdate', 'date', 'dc.date') or a.get('property') == 'article:published_time'): self.published = a.get('content', '')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip = max(0, self.skip - 1)
        if tag == 'title': self.in_title = False
        if tag == 'p': self.parts.append('\n')

    def handle_data(self, value):
        if self.in_title: self.title += value
        elif not self.skip: self.parts.append(value)


def allowed(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('http', 'https') or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('仅支持白名单机构的 HTTP/HTTPS 标准端口')
    for source in CONFIG['sources']:
        if p.hostname == source['host']: return source
    raise ValueError('域名不在官方来源白名单内；请先在 config.json 中人工确认并配置')


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, check_robots=True):
    allowed(url)
    opener = urllib.request.build_opener(SafeRedirect())
    if check_robots:
        p = urllib.parse.urlsplit(url)
        robots_url = f'{p.scheme}://{p.netloc}/robots.txt'
        try:
            with opener.open(urllib.request.Request(robots_url, headers={'User-Agent': AGENT}), timeout=20) as r:
                rules = urllib.robotparser.RobotFileParser(); rules.parse(r.read(262144).decode('utf-8', 'replace').splitlines())
            if not rules.can_fetch(AGENT, url): raise ValueError('robots.txt 不允许采集此地址')
        except urllib.error.HTTPError as e:
            if e.code != 404: raise ValueError(f'无法确认 robots.txt：HTTP {e.code}')
    with opener.open(urllib.request.Request(url, headers={'User-Agent': AGENT}), timeout=30) as r:
        raw = r.read(CONFIG['max_bytes'] + 1)
        if len(raw) > CONFIG['max_bytes']: raise ValueError('文件超过大小限制')
        return raw, r.headers.get('Content-Type', ''), r.headers.get_content_charset()


def parse(raw, content_type, charset=None):
    if raw.startswith(b'%PDF'):
        try: import pymupdf
        except ImportError: raise ValueError('PDF 解析需要安装 pymupdf；原始文件已保存，可安装后重试')
        with pymupdf.open(stream=raw, filetype='pdf') as pdf:
            text = '\n'.join(f'\n[第{i+1}页]\n{p.get_text()}' for i, p in enumerate(pdf))
        return 'PDF 文献', text, [], [], '', 'PDF/待分类'
    if content_type.startswith('image/'):
        try:
            import pytesseract
            from PIL import Image
            text = pytesseract.image_to_string(Image.open(io.BytesIO(raw)), lang='chi_sim')
        except (ImportError, Exception) as e: raise ValueError(f'图片 OCR 需要 Pillow、pytesseract 和中文 Tesseract：{e}')
        return '图片文献', text, [], [], '', '图片/待分类'
    html = None
    for enc in [charset, 'utf-8', 'gb18030']:
        if enc:
            try: html = raw.decode(enc); break
            except (UnicodeDecodeError, LookupError): pass
    if html is None: raise ValueError('无法识别文本编码')
    page = Page(); page.feed(html)
    # 优先正文容器；缺少容器时保留全文并要求核对。
    match = re.search(r'<(?:div|article)[^>]*(?:id|class)=[\"\'][^\"\']*(?:TRS_Editor|article-content|article_content|show_text|text_show|content_body)[^\"\']*[\"\'][^>]*>([\s\S]*?)</(?:article|div)>', html, re.I)
    body_page = Page()
    if match: body_page.feed(match.group(1))
    else: body_page = page
    body = '\n'.join(x.strip() for x in ''.join(body_page.parts).splitlines() if x.strip())
    kind = '排除候选/报道通知' if re.search('公告|通知|新闻|开幕|活动举行|征集', page.title) else '史料整理/待分类'
    return page.title.strip(), body, body_page.images, page.links, page.published, kind


UNITS = ['中央红军', '红一方面军', '红二方面军', '红四方面军', '红二十五军', '红二军团', '红六军团']
DATE = re.compile(r'(?:(193[4-6])年)?(\d{1,2})月(?:(\d{1,2})(?:日|日至\d{1,2}日))?')


def extract(conn, doc_id, body):
    # 只提取明确写出 1934—1936 年的句子；不从网页发布日期推断历史年份。
    for n, sentence in enumerate(re.split(r'(?<=[。！？])|\n', body), 1):
        matches = list(DATE.finditer(sentence))
        dated = [m for m in matches if m.group(1)]
        if not dated: continue
        m = dated[0]
        if not 1 <= int(m.group(2)) <= 12: continue
        if m.group(3) and not 1 <= int(m.group(3)) <= 31: continue
        event_type = next((v for k, v in [('战', '战役/战斗'), ('会议', '会议'), ('会师', '会师'), ('渡', '行军/渡河'), ('出发', '出发'), ('长征', '行军')] if k in sentence), '其他')
        values = (doc_id, sentence[:70], m.group(0), int(m.group(1)), '日' if m.group(3) else '月',
                  json.dumps([x for x in UNITS if x in sentence], ensure_ascii=False),
                  json.dumps([x for x in CONFIG['people'] if x in sentence], ensure_ascii=False),
                  json.dumps([x for x in CONFIG['places'] if x in sentence], ensure_ascii=False), event_type,
                  sentence.strip(), f'正文句段 {n}')
        conn.execute('INSERT INTO events(document_id,name,date_text,year,date_precision,units,people,places,event_type,evidence,locator) VALUES(?,?,?,?,?,?,?,?,?,?,?)', values)


def collect(url, download_images=True):
    source = allowed(url)
    doc_id = hashlib.sha256(url.encode()).hexdigest()[:20]
    with LOCK:
        with db() as conn:
            existing = conn.execute('SELECT id FROM documents WHERE url=?', (url,)).fetchone()
            if existing: return {'id': existing['id'], 'message': '已采集，保留已有核对记录'}
        raw, content_type, charset = download(url)
        folder = DATA / 'raw'; folder.mkdir(exist_ok=True)
        path = folder / (doc_id + '.bin'); path.write_bytes(raw)
        title, body, images, links, published, kind = parse(raw, content_type, charset)
        if '长征' not in body: kind = '排除候选/主题不符'
        for item in images:
            item['url'] = urllib.parse.urljoin(url, item['url']); item['local_path'] = ''; item['error'] = ''
            if not download_images:
                item['error'] = '本轮优先采集文字，图片原始链接已记录，待补下载'
                continue
            try:
                time.sleep(1)
                image_raw, _, _ = download(item['url'])
                image_path = folder / (hashlib.sha256(image_raw).hexdigest() + '.img')
                image_path.write_bytes(image_raw); item['local_path'] = str(image_path.relative_to(DATA))
            except Exception as e: item['error'] = str(e)
        with db() as conn:
            conn.execute('INSERT INTO documents(id,url,title,institution,kind,published,collected,sha256,raw_path,body,images) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (doc_id, url, title, source['institution'], kind, published, datetime.now().isoformat(), hashlib.sha256(raw).hexdigest(), str(path.relative_to(DATA)), body, json.dumps(images, ensure_ascii=False)))
            if not kind.startswith('排除'): extract(conn, doc_id, body)
        return {'id': doc_id, 'message': '采集完成；请核对文献性质与正文完整性'}


def run_job(job_id, url):
    try: result = collect(url); state, message = '完成', result['message']
    except Exception as e: state, message = '失败', str(e)
    with db() as conn: conn.execute('UPDATE jobs SET state=?,message=? WHERE id=?', (state, message, job_id))


class Handler(BaseHTTPRequestHandler):
    def send(self, obj, status=200, content_type='application/json; charset=utf-8'):
        raw = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type', content_type); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        p = urllib.parse.urlsplit(self.path); q = urllib.parse.parse_qs(p.query)
        if p.path == '/': return self.send((ROOT / 'index.html').read_bytes(), content_type='text/html; charset=utf-8')
        with db() as conn:
            if p.path == '/api/sources': return self.send(CONFIG['sources'])
            if p.path == '/api/crawl':
                import crawler
                return self.send(crawler.snapshot())
            if p.path == '/api/jobs': return self.send([dict(r) for r in conn.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 50')])
            if p.path == '/api/documents':
                term = '%' + q.get('q', [''])[0] + '%'
                return self.send([dict(r) for r in conn.execute('SELECT * FROM documents WHERE title LIKE ? OR body LIKE ? ORDER BY collected DESC', (term, term))])
            if p.path in ('/api/events', '/api/export'):
                rows = [dict(r) for r in conn.execute("SELECT e.*,d.title AS document_title,d.url AS source_url FROM events e JOIN documents d ON d.id=e.document_id WHERE d.status!='排除' ORDER BY year,date_text,e.id")]
                if p.path == '/api/export':
                    export = DATA / 'exports'; export.mkdir(exist_ok=True)
                    (export / 'events.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
                    if rows:
                        with (export / 'events.csv').open('w', encoding='utf-8-sig', newline='') as f:
                            writer = csv.DictWriter(f, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
                    (export / 'timeline.md').write_text('\n\n'.join(f"- {r['date_text']} {r['name']} [{r['status']}]\n  证据：{r['evidence']}\n  来源：{r['source_url']}" for r in rows), encoding='utf-8')
                return self.send(rows)
        self.send({'error': '不存在'}, 404)

    def do_POST(self):
        # 拒绝跨站请求，避免其他网站调用本机写入接口。
        if self.headers.get('Origin') not in (None, 'http://127.0.0.1:8765', 'http://localhost:8765'):
            return self.send({'error': '拒绝跨站请求'}, 403)
        try:
            size = int(self.headers.get('Content-Length', 0))
            if size > 100000: raise ValueError('请求过大')
            payload = json.loads(self.rfile.read(size))
            if self.path == '/api/crawl':
                import crawler
                return self.send(crawler.start(payload.get('limit', 200), payload.get('depth', 4), payload.get('retry', False)))
            if self.path == '/api/crawl/stop':
                import crawler
                crawler.stop.set()
                return self.send({'message': '已请求暂停；当前页面完成后停止'})
            with db() as conn:
                if self.path == '/api/collect':
                    url = payload['url'].strip(); allowed(url)
                    cursor = conn.execute('INSERT INTO jobs(url,state,created) VALUES(?,?,?)', (url, '处理中', datetime.now().isoformat()))
                    conn.commit(); threading.Thread(target=run_job, args=(cursor.lastrowid, url), daemon=True).start()
                    return self.send({'message': '任务已加入队列'})
                if self.path == '/api/review':
                    table = 'events' if payload['type'] == 'event' else 'documents'
                    if payload['status'] not in ('待核对', '已确认', '排除'): raise ValueError('状态无效')
                    conn.execute(f'UPDATE {table} SET status=? WHERE id=?', (payload['status'], payload['id']))
                    if table == 'events': conn.execute('UPDATE events SET name=?,notes=? WHERE id=?', (payload['name'], payload.get('notes', ''), payload['id']))
                    return self.send({'message': '已保存'})
            raise ValueError('未知接口')
        except Exception as e: self.send({'error': str(e)}, 400)


if __name__ == '__main__':
    with db(): pass
    print(f'本机地址：http://127.0.0.1:8765\n数据目录：{DATA}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8765), Handler).serve_forever()

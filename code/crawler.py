"""官方来源链接图与 sitemap 检索。队列持久化；单工作线程。"""
import json
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser
import app

guard = threading.Lock()
stop = threading.Event()
KEYWORDS = ['长征', '红一方面军', '红二方面军', '红四方面军', '红二十五军', '红二军团', '红六军团', '遵义会议', '四渡赤水', '两河口会议', '俄界会议', '吴起会师', '会宁会师']


def connection():
    c = app.db()
    c.executescript('''CREATE TABLE IF NOT EXISTS crawl_queue(
      url TEXT PRIMARY KEY, parent TEXT, depth INTEGER, state TEXT DEFAULT '待检索',
      attempts INTEGER DEFAULT 0, title TEXT DEFAULT '', matched TEXT DEFAULT '', error TEXT DEFAULT '', updated TEXT);
      CREATE TABLE IF NOT EXISTS crawl_meta(key TEXT PRIMARY KEY,value TEXT);''')
    return c


def canonical(url):
    p = urllib.parse.urlsplit(url)
    app.allowed(url)
    if any(x in (p.path + '?' + p.query).lower() for x in ('logout', 'login', 'javascript:', '/search?', '/search/', 'mailto:')): return None
    if p.path.lower().endswith(('.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.zip', '.mp4', '.mp3', '.ico', '.woff')): return None
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, ''))


def enqueue(c, url, parent='', depth=0):
    try: url = canonical(url)
    except ValueError: return
    if url:
        c.execute('INSERT OR IGNORE INTO crawl_queue(url,parent,depth,updated) VALUES(?,?,?,?)', (url, parent, depth, datetime.now().isoformat()))


class Links(HTMLParser):
    def __init__(self): super().__init__(); self.href = None; self.text = []; self.rows = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a': self.href = dict(attrs).get('href'); self.text = []
    def handle_data(self, text):
        if self.href: self.text.append(text)
    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            self.rows.append((self.href, ''.join(self.text))); self.href = None


def snapshot():
    with connection() as c:
        counts = {r['state']: r['n'] for r in c.execute('SELECT state,count(*) n FROM crawl_queue GROUP BY state')}
        recent = [dict(r) for r in c.execute('SELECT * FROM crawl_queue ORDER BY updated DESC LIMIT 30')]
    return {'running': guard.locked(), 'counts': counts, 'recent': recent,
            'scope': '已配置官方站点的种子、首页、sitemap 与可达链接；不保证覆盖孤立页面、登录页面和动态搜索数据库'}


def start(limit=200, depth=4, retry=False):
    limit, depth = int(limit), int(depth)
    if not 1 <= limit <= 100000 or not 0 <= depth <= 30: raise ValueError('页数应为1—100000，深度应为0—30')
    if not guard.acquire(blocking=False): raise ValueError('已有自动检索任务运行')
    stop.clear()
    try:
        with connection() as c:
            c.execute("UPDATE crawl_queue SET state='待检索' WHERE state='处理中'")
            if retry: c.execute("UPDATE crawl_queue SET state='待检索' WHERE state='失败' AND attempts<3")
            for source in app.CONFIG['sources']:
                base = 'https://' + source['host']
                for url in source['seeds'] + [base+'/', base+'/sitemap.xml']:
                    enqueue(c, url)
        threading.Thread(target=worker, args=(limit, depth), daemon=True).start()
    except Exception:
        guard.release(); raise
    return {'message': '自动检索已启动；达到本轮页数上限后可继续，队列与失败记录保留'}


def worker(limit, max_depth):
    try:
        for _ in range(limit):
            if stop.is_set(): break
            with connection() as c:
                row = c.execute("SELECT * FROM crawl_queue WHERE state='待检索' AND depth<=? ORDER BY depth,url LIMIT 1", (max_depth,)).fetchone()
                if not row: break
                url = row['url']
                c.execute("UPDATE crawl_queue SET state='处理中',attempts=attempts+1,updated=? WHERE url=?", (datetime.now().isoformat(), url))
            state, error, title, hits = '已检索', '', '', []
            try:
                raw, content_type, charset = app.download(url)
                with connection() as c:
                    if 'xml' in content_type or raw.lstrip().startswith((b'<?xml', b'<urlset', b'<sitemapindex')):
                        tree = ET.fromstring(raw)
                        for node in tree.iter():
                            if node.tag.rsplit('}', 1)[-1] == 'loc' and node.text:
                                enqueue(c, node.text.strip(), url, row['depth'] + 1)
                    else:
                        title, body, images, _, _, kind = app.parse(raw, content_type, charset)
                        hits = [k for k in KEYWORDS if k in title + '\n' + body]
                        if 'html' in content_type:
                            html = None
                            for enc in [charset, 'utf-8', 'gb18030']:
                                if enc:
                                    try: html = raw.decode(enc); break
                                    except (UnicodeError, LookupError): pass
                            links = Links(); links.feed(html or '')
                            for href, text in links.rows:
                                enqueue(c, urllib.parse.urljoin(url, href), url, row['depth'] + 1)
                # 首页、目录及分页用于发现，文章型地址或标题命中才采集。
                path = urllib.parse.urlsplit(url).path.lower()
                article = bool(any(k in title for k in KEYWORDS) or re.search(r'(?:/n1/\d{4}/|/t\d|\d{5,}\.s?html?$)', path))
                if hits and article:
                    if kind.startswith('排除'): state = '排除候选'
                    else: app.collect(url); state = '已采集'
            except Exception as e: state, error = '失败', str(e)
            with connection() as c:
                c.execute('UPDATE crawl_queue SET state=?,error=?,title=?,matched=?,updated=? WHERE url=?',
                          (state, error, title, json.dumps(hits, ensure_ascii=False), datetime.now().isoformat(), url))
            stop.wait(1.5)
    finally: guard.release()


if __name__ == '__main__':
    import sys
    print(start(int(sys.argv[1]) if len(sys.argv)>1 else 200))
    while guard.locked(): time.sleep(1)
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))

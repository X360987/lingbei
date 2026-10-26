import sqlite3
import unittest
from unittest.mock import patch
import app
import crawler


class DiscoveryTests(unittest.TestCase):
    def test_links_pagination_and_text(self):
        p = crawler.Links(); p.feed('<a href="index_2.html">下一页</a><a href="a.html">红二十五军长征</a>')
        self.assertEqual(p.rows[0], ('index_2.html', '下一页'))
        self.assertIn('红二十五军', p.rows[1][1])

    def test_url_filter(self):
        self.assertEqual(crawler.canonical('https://www.dswxyjy.org.cn/a.html#part'), 'https://www.dswxyjy.org.cn/a.html')
        self.assertIsNone(crawler.canonical('https://www.dswxyjy.org.cn/a.png'))
        with self.assertRaises(ValueError): crawler.canonical('https://example.com/a')

    def test_worker_persists_discovery_and_failure(self):
        c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE crawl_queue(url PRIMARY KEY,parent,depth,state DEFAULT '待检索',attempts DEFAULT 0,title DEFAULT '',matched DEFAULT '',error DEFAULT '',updated)")
        crawler.enqueue(c, app.CONFIG['sources'][0]['seeds'][0])
        crawler.guard.acquire(); crawler.stop.clear()
        html = '<title>长征史料</title><p>长征</p><a href="/next.html">下一页</a>'.encode()
        try:
            with patch.object(crawler, 'connection', return_value=c), patch.object(app, 'download', return_value=(html,'text/html','utf-8')), patch.object(app, 'collect', return_value={}), patch.object(crawler.stop, 'wait'):
                crawler.worker(1, 3)
            self.assertEqual(c.execute("SELECT count(*) FROM crawl_queue WHERE state='已采集'").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM crawl_queue WHERE state='待检索'").fetchone()[0], 1)
        finally: c.close()

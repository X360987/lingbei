import sys
import tempfile
import sqlite3
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class ArchiveTests(unittest.TestCase):
    def test_reject_untrusted_and_credentials(self):
        for url in ['http://127.0.0.1/a', 'file:///etc/passwd', 'https://www.dswxyjy.org.cn.evil.test/a', 'https://x@www.dswxyjy.org.cn/a']:
            with self.assertRaises(ValueError): app.allowed(url)

    def test_history_dates_and_evidence(self):
        old = app.db
        connection = sqlite3.connect(':memory:')
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,document_id,name,date_text,year,date_precision,units,people,places,event_type,evidence,locator,status DEFAULT "待核对")')
        app.db = lambda: connection
        if True:
            try:
                with app.db() as c:
                    app.extract(c, 'fixture', '发布日期2026年10月22日。1934年11月16日，红二十五军从何家冲出发。11月17日继续行军。')
                    rows = c.execute('SELECT * FROM events').fetchall()
                    self.assertEqual(len(rows), 1)
                    self.assertIn('何家冲', rows[0]['places'])
                    self.assertIn('红二十五军', rows[0]['units'])
                    self.assertEqual(rows[0]['status'], '待核对')
                    self.assertIn('1934年11月16日', rows[0]['evidence'])
            finally:
                app.db = old
                connection.close()

    def test_html_and_image_caption(self):
        result = app.parse('<title>史料</title><div class="TRS_Editor"><p>长征正文</p><img src="a.jpg" alt="历史图片"></div><script>noise</script>'.encode(), 'text/html')
        self.assertEqual(result[0], '史料')
        self.assertIn('长征正文', result[1])
        self.assertNotIn('noise', result[1])
        self.assertEqual(result[2][0]['caption'], '历史图片')


if __name__ == '__main__': unittest.main()

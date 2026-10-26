"""启动临时本机服务，验证页面、查询、导出，并退出。"""
import json
import threading
import urllib.request
import app

server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_address[1]}'
try:
    for path in ['/', '/api/sources', '/api/documents', '/api/events', '/api/export']:
        with urllib.request.urlopen(base + path, timeout=5) as response:
            body = response.read()
            assert response.status == 200
            if path != '/': json.loads(body)
            print(path, 'OK', len(body))
finally:
    server.shutdown()
    server.server_close()

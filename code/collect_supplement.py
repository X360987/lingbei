"""定向补充已检索确认的官方史料；每篇独立记录结果。"""
import json
from datetime import datetime
import app

SOURCES = [
    {'host':'www.ccdi.gov.cn','institution':'中央纪委国家监委','seeds':['https://www.ccdi.gov.cn/special/jdybzn/yaowen_jdybn/202106/t20210628_244895.html']},
    {'host':'www.ccphistory.org.cn','institution':'中共上海市委党史研究室','seeds':['https://www.ccphistory.org.cn/shds/shhm/content/49e64f68-b830-4d5d-8882-3ae4b9d0a3c8.html']},
]

if __name__ == '__main__':
    config = app.CONFIG
    for source in SOURCES:
        if not any(s['host']==source['host'] for s in config['sources']): config['sources'].append(source)
    (app.ROOT/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    urls = [config['sources'][1]['seeds'][0]] + [s['seeds'][0] for s in SOURCES] + [
        'https://www.dswxyjy.org.cn/n1/2016/0929/c218998-28749730.html',
        'https://www.dswxyjy.org.cn/n1/2016/1107/c349473-28841860.html',
    ]
    report = []
    for url in urls:
        try:
            result = app.collect(url, download_images=False)
            result.update(url=url,state='成功')
        except Exception as e: result = {'url':url,'state':'失败','error':str(e)}
        report.append(result)
        folder=app.DATA/'采集记录'; folder.mkdir(exist_ok=True)
        (folder/'本轮补充.json').write_text(json.dumps({'collected_at':datetime.now().isoformat(),'results':report},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=True),flush=True)

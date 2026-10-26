"""导出资料库清单，区分采集和核对状态。"""
import json
import re
import app

with app.db() as c:
    rows=[dict(r) for r in c.execute('SELECT * FROM documents ORDER BY collected')]
lines=['# 本轮采集及现有文献清单','','新增3篇，现有4篇。新文献保持待核对；已保存网页原件及正文，本轮图片仅保留原始链接，待补下载。','']
for d in rows:
    source = re.search(r'来源[：:]([^\n]+)',d['body'])
    lines += [f"## {d['title']}",'',f"- 官方页面：{d['url']}",f"- 发布机构：{d['institution']}",f"- 原文标注来源：{source.group(1) if source else '未识别，需人工查看原件'}",f"- 核对状态：{d['status']}",f"- 正文字符数：{len(d['body'])}",f"- 文献编号：{d['id']}",f"- 本地原件：{d['raw_path']}",'']
lines += ['## 本轮未成功采集','','- 湖北党史网红二十五军资料：服务器证书域名不匹配。','- 中央纪委国家监委《中国共产党一百年大事记》：robots.txt 访问异常。','', '原有节点归类文件尚未随新增资料重建，避免将旧节点规则误当作完整分析。']
folder=app.DATA/'采集记录';folder.mkdir(exist_ok=True)
(folder/'文献清单.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps([{'title':d['title'],'characters':len(d['body']),'status':d['status']} for d in rows],ensure_ascii=True))

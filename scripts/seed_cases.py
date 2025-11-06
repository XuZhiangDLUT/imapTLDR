from email.message import EmailMessage
from email.utils import formatdate
from imapclient import IMAPClient
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
cfg = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
user = cfg['imap']['email']
password = cfg['imap']['password']

cases = []
# 1. plain text
msg = EmailMessage(); msg['Subject']='CASE1_PLAIN'; msg['From']=user; msg['To']=user; msg['Date']=formatdate(localtime=True)
msg.set_content('纯文本：这是一段用于功能测试的纯文本。This is a plain text paragraph.')
cases.append(('INBOX', msg))
# 2. rich html with link/style
html = """<html><body><p style='color:blue'>带 <strong>加粗</strong> 与 <em>斜体</em> 的 <a href='https://example.com'>链接</a>。</p></body></html>"""
msg = EmailMessage(); msg['Subject']='CASE2_RICH'; msg['From']=user; msg['To']=user; msg['Date']=formatdate(localtime=True)
msg.set_content('fallback'); msg.add_alternative(html, subtype='html')
cases.append(('INBOX', msg))
# 3. with blockquote should be skipped
html = """<html><body><blockquote><p>引用历史不应翻译</p></blockquote><p>应当被翻译的一段。</p></body></html>"""
msg = EmailMessage(); msg['Subject']='CASE3_QUOTE'; msg.set_content('fb'); msg.add_alternative(html, subtype='html'); msg['From']=user; msg['To']=user; msg['Date']=formatdate(localtime=True)
cases.append(('INBOX', msg))
# 4. keyword hit subject
msg = EmailMessage(); msg['Subject']='相关研究汇总 - 快测'; msg.set_content('关键词命中'); msg['From']=user; msg['To']=user; msg['Date']=formatdate(localtime=True)
cases.append(('INBOX', msg))
# 5. keyword hit sender
msg = EmailMessage(); msg['Subject']='X'; msg.set_content('发件人命中'); msg['From']='scholaralerts-noreply@google.com'; msg['To']=user; msg['Date']=formatdate(localtime=True)
cases.append(('INBOX', msg))

c = IMAPClient('imap.qq.com', ssl=True)
c.login(user, password)
for folder, m in cases:
    c.append(folder, m.as_bytes(), flags=())
print('seeded', len(cases), 'mails')
c.logout()

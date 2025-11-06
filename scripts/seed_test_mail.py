from email.message import EmailMessage
from email.utils import formatdate
from imapclient import IMAPClient
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
user = CFG['imap']['email']
password = CFG['imap']['password']

html = """
<html>
  <body>
    <h1>测试邮件标题</h1>
    <p>这是一段用于翻译功能测试的正文内容。</p>
    <p>第二段包含一些标点符号与英文 Terms for mock translation.</p>
  </body>
</html>
"""

msg = EmailMessage()
msg['Subject'] = 'TEST_TRANSLATE 示例邮件'
msg['From'] = user
msg['To'] = user
msg['Date'] = formatdate(localtime=True)
msg.set_content('纯文本备用')
msg.add_alternative(html, subtype='html')

c = IMAPClient('imap.qq.com', ssl=True)
c.login(user, password)
c.append('INBOX', msg.as_bytes(), flags=())
c.logout()
print('seeded one test message into INBOX (UNSEEN)')

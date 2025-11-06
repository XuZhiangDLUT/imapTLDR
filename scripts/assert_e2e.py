# Programmatic assertions for recent appended mails
from email.parser import BytesParser
from email import policy
from imapclient import IMAPClient
from pathlib import Path
import json
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
cfg = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
user = cfg['imap']['email']
password = cfg['imap']['password']

c = IMAPClient('imap.qq.com', ssl=True)
c.login(user, password)

# scan INBOX recent
c.select_folder('INBOX')
uids = c.search(['ALL'])
recent = uids[-50:] if len(uids) > 50 else uids
headers = c.fetch(recent, [b'BODY.PEEK[HEADER]'])

translated = []
summarized = []
for uid in recent:
    hdr = BytesParser(policy=policy.default).parsebytes(headers[uid][b'BODY[HEADER]'])
    sub = str(hdr.get('Subject','') or '')
    if sub.startswith('[机器翻译]'):
        translated.append((uid, hdr))
    if sub.startswith('[机器总结]'):
        summarized.append((uid, hdr))

assert len(translated) > 0 or len(summarized) > 0, 'no test mails found'

# assert translated has In-Reply-To and X-Linked-Message-Id (check latest auto-generated ones)
autos = [(u, h) for (u, h) in translated if (h.get('Auto-Submitted') == 'auto-generated')]
autos = autos[-3:] if len(autos) > 3 else autos
for uid, hdr in autos:
    assert hdr.get('In-Reply-To'), 'missing In-Reply-To'
    assert hdr.get('X-Linked-Message-Id'), 'missing X-Linked-Message-Id'

# fetch one translated body to check clone structure exists (rough)
if translated:
    uid, _ = (autos[-1] if autos else translated[-1])
    body = c.fetch([uid], [b'BODY.PEEK[]'])[uid][b'BODY[]']
    msg = BytesParser(policy=policy.default).parsebytes(body)
    html = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                html = part.get_content(); break
    if html:
        soup = BeautifulSoup(html, 'html5lib')
        # two adjacent blocks (original + clone) roughly by <br/> separating
        brs = soup.find_all('br')
        assert len(brs) >= 1, 'no <br> separators injected'

print('assertions ok: translated=%d summarized=%d' % (len(translated), len(summarized)))

c.logout()

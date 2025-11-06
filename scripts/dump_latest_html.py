# Dump latest INBOX emails' HTML bodies into data/raw for debugging injection
from pathlib import Path
from email.parser import BytesParser
from email import policy
from imapclient import IMAPClient
import json
import sys

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
user = CFG['imap']['email']
password = CFG['imap']['password']
server = CFG['imap'].get('server','imap.qq.com')
port = int(CFG['imap'].get('port', 993))
ssl = bool(CFG['imap'].get('ssl', True))

outdir = ROOT / 'data' / 'raw'
outdir.mkdir(parents=True, exist_ok=True)

c = IMAPClient(server, port=port, ssl=ssl)
c.login(user, password)

c.select_folder('INBOX')
uids = c.search(['ALL'])
latest = uids[-10:] if len(uids) > 10 else uids

for uid in latest:
    raw = c.fetch([uid], [b'BODY.PEEK[]'])[uid][b'BODY[]']
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    html = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                html = part.get_content(); break
    else:
        if msg.get_content_type() == 'text/html':
            html = msg.get_content()
    if not html:
        continue
    path = outdir / f'INBOX-{uid}.html'
    path.write_text(html, encoding='utf-8', errors='ignore')
    print('wrote', path)

c.logout()
print('done.')

from imapclient import IMAPClient
from email.parser import BytesParser
from email import policy
import json
import pathlib

root = pathlib.Path(__file__).resolve().parent.parent
cfg = json.loads((root / 'config.json').read_text(encoding='utf-8'))
user = cfg['imap']['email']
password = cfg['imap']['password']

c = IMAPClient('imap.qq.com', ssl=True)
c.login(user, password)
c.select_folder('INBOX')
uids = c.search(['ALL'])
uids = sorted(uids)[-50:]
res = []
for uid in uids:
    data = c.fetch([uid],[b'BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)]'])
    hdr = data[uid][b'BODY[HEADER.FIELDS (SUBJECT FROM DATE)]']
    msg = BytesParser(policy=policy.default).parsebytes(hdr)
    sub = str(msg.get('Subject',''))
    if sub.startswith('[机器翻译]') or sub.startswith('[机器总结]'):
        res.append((uid, sub))
print('found', len(res), 'messages with prefixes')
for uid, sub in res[-10:]:
    print(uid, sub)

c.logout()

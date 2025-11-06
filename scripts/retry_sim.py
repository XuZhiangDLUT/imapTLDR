# Simulate half-way failure: translated appended but original still UNSEEN
from email.message import EmailMessage
from email.utils import formatdate
from imapclient import IMAPClient
from email.parser import BytesParser
from email import policy
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
cfg = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
user = cfg['imap']['email']
password = cfg['imap']['password']

# 1) seed original
from email.utils import make_msgid

orig = EmailMessage(); orig['Subject']='RETRY_ORIG'; orig['From']=user; orig['To']=user; orig['Date']=formatdate(localtime=True)
mid = make_msgid()
orig['Message-ID'] = mid
orig.set_content('for idempotency test');

c = IMAPClient('imap.qq.com', ssl=True)
c.login(user, password)
c.append('INBOX', orig.as_bytes(), flags=())

# 2) append fake translated referencing orig, but keep orig UNSEEN
fake = EmailMessage()
fake['Subject'] = '[机器翻译] retry linked'
fake['From'] = user
fake['To'] = user
fake['Date'] = formatdate(localtime=True)
fake['In-Reply-To'] = mid
fake['References'] = mid
fake['X-Linked-Message-Id'] = mid
fake.set_content('mock')
c.append('INBOX', fake.as_bytes(), flags=())

# 3) run translate_job(mock) and ensure not duplicated
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from mailbot.jobs import translate_job
from mailbot.config import load_config
cfg = load_config()
cfg.setdefault('llm', {})['mock'] = True
cfg.setdefault('translate', {})['folders'] = ['INBOX']
cfg['translate']['max_per_run_per_folder'] = 3
translate_job(cfg)

# 4) count translations linked to mid
c.select_folder('INBOX')
uids = c.search(['ALL'])
recent = uids[-50:] if len(uids) > 50 else uids
headers = c.fetch(recent, [b'BODY.PEEK[HEADER]'])
count = 0
for u in recent:
    hdr = BytesParser(policy=policy.default).parsebytes(headers[u][b'BODY[HEADER]'])
    subj = str(hdr.get('Subject',''))
    xlink = hdr.get('X-Linked-Message-Id','')
    if subj.startswith('[机器翻译]'):
        print('candidate:', u, subj, 'xlink=', xlink)
    if xlink == mid and subj.startswith('[机器翻译]'):
        count += 1

print('orig mid:', mid)
print('linked translations:', count)
assert count == 1, 'idempotency failed: duplicate translations created'

c.logout()
print('retry_sim ok')

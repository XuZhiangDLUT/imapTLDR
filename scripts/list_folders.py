from imapclient import IMAPClient
import json
from pathlib import Path
from email.header import decode_header

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
user = CFG['imap']['email']
password = CFG['imap']['password']

out_txt = ROOT / 'folders.txt'
out_json = ROOT / 'folders.json'

c = IMAPClient('imap.qq.com', ssl=True)
c.login(user, password)

rows = c.list_folders()  # [(flags, delimiter, name), ...]
folders = [r[2] for r in rows]

# write plain text (one per line)
out_txt.write_text("\n".join(folders), encoding='utf-8')
# write json
out_json.write_text(json.dumps({'folders': folders}, ensure_ascii=False, indent=2), encoding='utf-8')

c.logout()
print(f"Wrote {len(folders)} folders to {out_txt.name} and {out_json.name}")

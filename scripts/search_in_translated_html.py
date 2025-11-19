import sys
from pathlib import Path
from email import policy
from email.parser import BytesParser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mailbot.imap_client import pick_html_or_text
from mailbot.utils import decode_subject


def main():
    if len(sys.argv) < 3:
        print("Usage: search_in_translated_html.py <eml_path> <needle>")
        return
    path = Path(sys.argv[1]).resolve()
    needle = sys.argv[2]

    raw = path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    sub = decode_subject(msg)
    print("Subject:", sub)
    html, text = pick_html_or_text(msg)
    print("HTML length:", len(html) if html else 0)

    if html and needle in html:
        print("Needle found in HTML.")
        idx = html.index(needle)
        start = max(0, idx - 200)
        end = idx + len(needle) + 200
        print(html[start:end])
    else:
        print("Needle NOT found in translated HTML.")


if __name__ == "__main__":
    main()

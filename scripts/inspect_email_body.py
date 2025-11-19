import sys
from pathlib import Path
from email import policy
from email.parser import BytesParser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mailbot.imap_client import pick_html_or_text
from mailbot.utils import decode_subject


def inspect(path: Path) -> None:
    raw = path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    sub = decode_subject(msg)
    date = msg.get("Date", "")
    print("===", path.name, "===")
    print("Subject:", sub)
    print("Date:", date)
    html, text = pick_html_or_text(msg)
    print("Has HTML:", bool(html), "Has text:", bool(text))
    if html:
        print("HTML snippet (first 1000 chars):")
        print(html[:1000])


def main():
    if len(sys.argv) < 2:
        print("Usage: inspect_email_body.py <eml_path>")
        return
    path = Path(sys.argv[1]).resolve()
    inspect(path)


if __name__ == "__main__":
    main()

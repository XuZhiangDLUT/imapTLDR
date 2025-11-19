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
        print("Usage: scan_phrase_occurrences.py <eml_path> <needle>")
        return
    path = Path(sys.argv[1]).resolve()
    needle = sys.argv[2]

    raw = path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    sub = decode_subject(msg)
    print("Subject:", sub)
    html, text = pick_html_or_text(msg)
    print("HTML length:", len(html) if html else 0)

    if not html:
        print("No HTML.")
        return

    idx = 0
    count = 0
    while True:
        pos = html.find(needle, idx)
        if pos == -1:
            break
        count += 1
        start = max(0, pos - 200)
        end = pos + len(needle) + 200
        print(f"\n--- occurrence {count} at pos {pos} ---")
        snippet = html[start:end]
        # write snippet to a utf-8 file instead of console to avoid encoding issues
        out_path = path.parent / f"snippet_{count}.txt"
        out_path.write_text(snippet, encoding="utf-8")
        print("Saved snippet to", out_path)
        idx = pos + len(needle)

    print("\nTotal occurrences:", count)


if __name__ == "__main__":
    main()

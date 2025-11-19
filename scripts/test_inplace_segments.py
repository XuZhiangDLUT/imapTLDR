import sys
from pathlib import Path
from email import policy
from email.parser import BytesParser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mailbot.imap_client import pick_html_or_text
from mailbot.utils import decode_subject
from mailbot.immersion import translate_html_inplace


def main():
    path = ROOT / "data" / "debug_emails" / "O-uid-5405.eml"
    raw = path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    sub = decode_subject(msg)
    print("Subject:", sub)
    html, text = pick_html_or_text(msg)
    print("Has HTML:", bool(html))

    collected: list[str] = []

    def spy_translator(batch: list[str]) -> list[str]:
        collected.extend(batch)
        # return dummy translations of same length
        return [f"TR[{i}]" for i, _ in enumerate(batch)]

    _ = translate_html_inplace(html, spy_translator)
    print("Total segments sent to translator:", len(collected))

    needle = "Reconfigurable Acoustic Coding Metasurface for Bidirectional Wavefront Manipulation across the Water-Air Interface"
    hits = [(i, s) for i, s in enumerate(collected) if needle in s]
    print("Segments containing needle:", len(hits))
    for index, s in hits:
        print(f"--- hit at batch index {index} ---")
        print(repr(s))


if __name__ == "__main__":
    main()

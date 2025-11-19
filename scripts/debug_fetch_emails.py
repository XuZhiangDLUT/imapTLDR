import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mailbot.imap_client import connect, fetch_raw, parse_message
from mailbot.utils import decode_subject

CFG_PATH = ROOT / "config.json"


def main():
    with CFG_PATH.open("r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    imap_cfg = cfg["imap"]
    user = imap_cfg["email"]
    password = imap_cfg["password"]
    server = imap_cfg["server"]
    port = imap_cfg.get("port", 993)
    ssl = imap_cfg.get("ssl", True)

    print("IMAP config loaded for", user)

    c = connect(server, user, password, port=port, ssl=ssl)
    try:
        print("Connected, selecting INBOX...")
        c.select_folder("INBOX")
        uids = c.search(["ALL"])
        print("Total UIDs in INBOX:", len(uids))

        sample = uids[-300:] if len(uids) > 300 else uids
        key = "Google Scholar"
        batch_tag = "(晚上批次 2)"

        originals = []
        translations = []
        for uid in sample:
            raw = fetch_raw(c, uid)
            msg = parse_message(raw)
            sub = decode_subject(msg)
            date = msg.get("Date", "")
            if key in sub and batch_tag in sub:
                if sub.startswith("[机器翻译]"):
                    translations.append((uid, sub, date))
                else:
                    originals.append((uid, sub, date))

        print("Found originals:")
        for uid, sub, date in originals:
            print("  UID", uid, "|", date, "|", sub)

        print("Found translations:")
        for uid, sub, date in translations:
            print("  UID", uid, "|", date, "|", sub)

        out_dir = ROOT / "data" / "debug_emails"
        out_dir.mkdir(parents=True, exist_ok=True)
        for uid, sub, date in originals:
            raw = fetch_raw(c, uid)
            fname = out_dir / f"O-uid-{uid}.eml"
            fname.write_bytes(raw)
            print("Saved original UID", uid, "to", fname)
        for uid, sub, date in translations:
            raw = fetch_raw(c, uid)
            fname = out_dir / f"T-uid-{uid}.eml"
            fname.write_bytes(raw)
            print("Saved translated UID", uid, "to", fname)
    finally:
        try:
            c.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()

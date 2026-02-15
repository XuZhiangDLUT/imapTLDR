from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mailbot.config import load_config
from mailbot.imap_client import (
    append_unseen,
    build_email,
    connect,
    fetch_raw,
    list_unseen,
    parse_message,
    pick_html_or_text,
)
from mailbot.jobs import translate_job
from mailbot.utils import decode_subject


OTHER_FOLDER_PREFIX = "\u5176\u4ed6\u6587\u4ef6\u5939/"


def normalize_folder(folder: str) -> str:
    if folder.startswith("INBOX") or "/" in folder:
        return folder
    return OTHER_FOLDER_PREFIX + folder


def seed_mail(cfg: dict, folder: str, subject: str) -> None:
    imap = cfg["imap"]
    html = (
        "<html><body>"
        "<p>Hello from temporary translation E2E script.</p>"
        "<p>This sentence should be translated into Chinese.</p>"
        "<p>Kind regards.</p>"
        "</body></html>"
    )
    raw = build_email(subject, imap["email"], imap["email"], html, None)
    c = connect(
        imap["server"],
        imap["email"],
        imap["password"],
        port=imap.get("port", 993),
        ssl=imap.get("ssl", True),
    )
    try:
        append_unseen(c, folder, raw)
    finally:
        c.logout()


def find_translated_mail(cfg: dict, folder: str, expected_subject: str) -> tuple[bool, str]:
    imap = cfg["imap"]
    c = connect(
        imap["server"],
        imap["email"],
        imap["password"],
        port=imap.get("port", 993),
        ssl=imap.get("ssl", True),
    )
    try:
        uids = list_unseen(c, folder)
        for uid in reversed(uids):
            raw = fetch_raw(c, uid)
            msg = parse_message(raw)
            sub = decode_subject(msg)
            if sub != expected_subject:
                continue
            html, text = pick_html_or_text(msg)
            body = (html or text or "")
            cjk_count = len(re.findall(r"[\u4e00-\u9fff]", body))
            return cjk_count > 0, f"uid={uid}, body_len={len(body)}, cjk_count={cjk_count}"
        return False, "translated mail not found in UNSEEN list"
    finally:
        c.logout()


def main() -> int:
    parser = argparse.ArgumentParser(description="Temporary E2E translation test for one mail.")
    parser.add_argument("--folder", default="")
    parser.add_argument("--max-per-run", type=int, default=1)
    parser.add_argument("--subject-prefix", default="DeepLX Tmp E2E")
    args = parser.parse_args()

    cfg = load_config()
    translate_folders = (cfg.get("translate", {}) or {}).get("folders") or []
    folder_key = args.folder or (translate_folders[0] if translate_folders else "INBOX")
    target_folder = normalize_folder(str(folder_key))

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    source_subject = f"{args.subject_prefix} {ts}"
    seed_mail(cfg, target_folder, source_subject)
    print(f"seeded_subject={source_subject}")
    print(f"seeded_folder={target_folder}")

    test_cfg = deepcopy(cfg)
    t = test_cfg.setdefault("translate", {})
    # Use original key form because translate_job normalizes non-INBOX folders.
    t["folders"] = [folder_key]
    t["max_per_run_per_folder"] = int(args.max_per_run)
    t["inbox_keywords"] = []
    t["inbox_from"] = []
    t["delete_translated_email"] = False
    t["force_retranslate"] = False

    print("running_translate_job=1")
    translate_job(test_cfg)
    print("running_translate_job=done")

    pref = (test_cfg.get("prefix", {}) or {}).get("translate", "[机器翻译]")
    expected_subject = f"{pref} {source_subject}"
    ok, detail = find_translated_mail(cfg, target_folder, expected_subject)
    print(f"expected_subject={expected_subject}")
    print(f"verification={detail}")
    if ok:
        print("result=PASS")
        return 0
    print("result=FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

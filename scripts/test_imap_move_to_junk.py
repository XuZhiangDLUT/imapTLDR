import argparse
import json
import pathlib
import sys
import time
from email import policy
from email.parser import BytesParser
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from imapclient import IMAPClient  # noqa: E402

from mailbot.imap_client import build_email, connect, find_system_junk_folder, move_to_junk  # noqa: E402


def load_config(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _mask_email(value: str) -> str:
    s = (value or "").strip()
    if "@" not in s:
        return "***"
    name, domain = s.split("@", 1)
    if len(name) <= 2:
        name_mask = name[:1] + "*"
    else:
        name_mask = name[:1] + "***" + name[-1:]
    return name_mask + "@" + domain


def _esc(s: str) -> str:
    return (s or "").encode("unicode_escape").decode("ascii")


def _folder_uidnext(client: IMAPClient, folder: str) -> int | None:
    try:
        status = client.folder_status(folder, [b"UIDNEXT"])  # type: ignore[arg-type]
        if isinstance(status, dict):
            nxt = status.get(b"UIDNEXT") or status.get("UIDNEXT")
            if isinstance(nxt, int):
                return nxt
    except Exception:
        return None
    return None


def _append_test_message_and_get_uid(client: IMAPClient, folder: str, subject: str) -> tuple[str, int]:
    """
    Append a unique test message and return (message_id, uid_in_folder).
    Best-effort: prefer APPENDUID / UIDNEXT; fallback to Message-ID search.
    """
    client.select_folder(folder)

    uidnext_before = _folder_uidnext(client, folder)
    msg = build_email(
        subject,
        "imapTLDR3-test@localhost",
        "imapTLDR3-test@localhost",
        "<html><body><p>imapTLDR3 move-to-junk test mail.</p></body></html>",
        None,
    )
    mid = str(msg.get("Message-ID") or "").strip()
    if not mid:
        raise RuntimeError("Generated test email missing Message-ID")

    append_ret = client.append(folder, msg.as_bytes(), flags=())

    # UIDPLUS fast path: some servers return APPENDUID (uidvalidity, uid)
    try:
        if isinstance(append_ret, tuple) and len(append_ret) == 2:
            _uidvalidity, appended_uid = append_ret
            if isinstance(appended_uid, int):
                return mid, appended_uid
            try:
                return mid, int(str(appended_uid).strip())
            except Exception:
                pass
    except Exception:
        pass

    uidnext_after = _folder_uidnext(client, folder)
    if (
        isinstance(uidnext_before, int)
        and isinstance(uidnext_after, int)
        and uidnext_after == uidnext_before + 1
    ):
        return mid, uidnext_before

    # Fallback: search by Message-ID (may lag briefly)
    want = {mid, mid.strip("<>")}
    for _ in range(10):
        client.select_folder(folder, readonly=True)
        for m in want:
            try:
                uids = client.search(["HEADER", "Message-ID", m])
            except Exception:
                continue
            if uids:
                u = int(uids[-1])
                return mid, u
        time.sleep(0.3)

    # Last resort: fetch recent headers and match Message-ID.
    client.select_folder(folder, readonly=True)
    cand_uids: list[int] = []
    if isinstance(uidnext_before, int) and isinstance(uidnext_after, int) and uidnext_after > uidnext_before:
        start = max(uidnext_before, uidnext_after - 200)
        cand_uids = list(range(start, uidnext_after))
    else:
        try:
            all_uids = client.search(["ALL"])
        except Exception:
            all_uids = []
        if all_uids:
            tail = all_uids[-200:] if len(all_uids) > 200 else all_uids
            cand_uids = [int(u) for u in tail if isinstance(u, int)]

    if cand_uids:
        try:
            data = client.fetch(cand_uids, [b"BODY.PEEK[HEADER]"])
        except Exception:
            data = {}
        for uid in cand_uids:
            try:
                raw_hdr = data[uid].get(b"BODY[HEADER]") or data[uid].get(b"BODY[]")
                if not raw_hdr:
                    continue
                hdr = BytesParser(policy=policy.default).parsebytes(raw_hdr)
                got = str(hdr.get("Message-ID") or "").strip()
                if got in want or got.strip("<>") in want:
                    return mid, int(uid)
            except Exception:
                continue

    raise RuntimeError(f"Could not locate appended test mail in folder={folder} (mid={mid})")


def _exists_by_uid(client: IMAPClient, folder: str, uid: int) -> bool:
    client.select_folder(folder, readonly=True)
    try:
        data = client.fetch([uid], [b"FLAGS"])
    except Exception:
        return False
    return isinstance(data, dict) and uid in data


def _find_by_message_id_fetch_tail(client: IMAPClient, folder: str, message_id: str, max_scan: int = 200) -> list[int]:
    client.select_folder(folder, readonly=True)
    try:
        uids = client.search(["ALL"])
    except Exception:
        return []
    if not uids:
        return []
    tail = uids[-max_scan:] if len(uids) > max_scan else uids
    try:
        data = client.fetch(tail, [b"BODY.PEEK[HEADER]"])
    except Exception:
        return []
    want = {str(message_id or "").strip(), str(message_id or "").strip("<>")}
    hits: list[int] = []
    for uid in tail:
        try:
            raw_hdr = data[uid][b"BODY[HEADER]"]
            hdr = BytesParser(policy=policy.default).parsebytes(raw_hdr)
            got = str(hdr.get("Message-ID") or "").strip()
            if got in want or got.strip("<>") in want:
                hits.append(int(uid))
        except Exception:
            continue
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description="Real IMAP smoke test: move a test message into system Junk/Spam.")
    parser.add_argument(
        "--config",
        default=str(REPO_ROOT / "config.json"),
        help="Path to config.json (default: repo root config.json)",
    )
    parser.add_argument(
        "--source-folder",
        default="INBOX",
        help="Folder to append the test mail into (default: INBOX).",
    )
    args = parser.parse_args()

    cfg = load_config(pathlib.Path(args.config))
    imap_cfg = cfg.get("imap") or {}
    if not isinstance(imap_cfg, dict):
        raise ValueError("config.json 缺少 imap 配置")

    host = str(imap_cfg.get("server") or "").strip()
    port = int(imap_cfg.get("port", 993) or 993)
    ssl = bool(imap_cfg.get("ssl", True))
    email = str(imap_cfg.get("email") or "").strip()
    password = str(imap_cfg.get("password") or "")
    if not host or not email or not password:
        raise ValueError("config.json 需要包含 imap.server / imap.email / imap.password")

    result: dict[str, Any] = {
        "source_folder": args.source_folder,
        "source_folder_esc": _esc(args.source_folder),
        "server": host,
        "port": port,
        "ssl": ssl,
        "email_masked": _mask_email(email),
    }

    c = connect(host, email, password, port=port, ssl=ssl)
    try:
        junk_folder = find_system_junk_folder(c)
        result["junk_folder_detected"] = junk_folder
        result["junk_folder_detected_esc"] = _esc(junk_folder or "")
        if not junk_folder:
            result["ok"] = False
            result["error"] = "Could not detect system Junk/Spam folder"
            print(json.dumps(result, ensure_ascii=True))
            return 2

        # Ensure folders are selectable.
        c.select_folder(args.source_folder, readonly=True)
        c.select_folder(junk_folder, readonly=True)

        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        subject = f"[imapTLDR3 test] move-to-junk {ts}"
        mid, uid = _append_test_message_and_get_uid(c, args.source_folder, subject)
        result["message_id"] = mid
        result["message_id_esc"] = _esc(mid)
        result["uid_in_source"] = uid
        result["exists_in_source_before"] = _exists_by_uid(c, args.source_folder, uid)
        result["junk_hits_before"] = _find_by_message_id_fetch_tail(c, junk_folder, mid)

        dst = move_to_junk(c, args.source_folder, uid)
        result["move_to_junk_return"] = dst
        result["move_to_junk_return_esc"] = _esc(dst)

        # Verify after move (allow brief server-side lag).
        time.sleep(0.4)
        result["exists_in_source_after"] = _exists_by_uid(c, args.source_folder, uid)
        result["junk_hits_after"] = _find_by_message_id_fetch_tail(c, dst, mid)

        result["ok"] = bool(result["junk_hits_after"])
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result["ok"] else 1
    finally:
        try:
            c.logout()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

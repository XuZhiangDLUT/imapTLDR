import argparse
import json
import pathlib
import sys
import time
from typing import Any
from email.parser import BytesParser
from email import policy

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mailbot.imap_client import build_email, connect, move_to_trash  # noqa: E402


def load_config(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _normalize_qq_folder(name: str) -> str:
    s = str(name or "").strip()
    if not s:
        return s
    # QQ 邮箱：其他文件夹通常以“其他文件夹/xxx”
    if s.startswith("INBOX") or "/" in s:
        return s
    return f"其他文件夹/{s}"


def _esc(s: str) -> str:
    return (s or "").encode("unicode_escape").decode("ascii")


def _folder_uidnext(client, folder: str) -> int | None:
    try:
        status = client.folder_status(folder, [b"UIDNEXT"])  # type: ignore[arg-type]
        if isinstance(status, dict):
            nxt = status.get(b"UIDNEXT") or status.get("UIDNEXT")
            if isinstance(nxt, int):
                return nxt
    except Exception:
        return None
    return None


def _append_test_message_and_get_uid(client, folder: str, subject: str, html: str, text: str | None) -> tuple[str, int]:
    """
    Append a unique test message and return (message_id, uid_in_folder).
    Best-effort: prefer UIDNEXT fast path; fallback to Message-ID search.
    """
    folder = _normalize_qq_folder(folder)
    client.select_folder(folder)

    uidnext_before = _folder_uidnext(client, folder)
    msg = build_email(subject, "imapTLDR3-test@localhost", "imapTLDR3-test@localhost", html, text)
    mid = str(msg.get("Message-ID") or "").strip()
    if not mid:
        raise RuntimeError("Generated test email missing Message-ID")

    append_ret = client.append(folder, msg.as_bytes(), flags=())

    uidnext_after = _folder_uidnext(client, folder)
    # UIDPLUS fast path: some servers return APPENDUID (uidvalidity, uid)
    try:
        if isinstance(append_ret, tuple) and len(append_ret) == 2:
            _uidvalidity, appended_uid = append_ret
            if isinstance(appended_uid, int):
                return mid, appended_uid
    except Exception:
        pass

    if (
        isinstance(uidnext_before, int)
        and isinstance(uidnext_after, int)
        and uidnext_after == uidnext_before + 1
    ):
        return mid, uidnext_before

    def _try_locate_by_search() -> list[int]:
        client.select_folder(folder)
        hits: list[int] = []
        for m in [mid, mid.strip("<>")]:
            try:
                uids = client.search(["HEADER", "Message-ID", m])
            except Exception:
                continue
            if uids:
                hits.extend([int(u) for u in uids if isinstance(u, int)])
        if uidnext_before is not None:
            hits = [u for u in hits if u >= uidnext_before]
        return sorted(set(hits))

    def _try_locate_by_fetch_recent() -> list[int]:
        # Prefer UIDNEXT bounds if available, otherwise fall back to the last N UIDs.
        client.select_folder(folder)
        cand_uids: list[int] = []
        if isinstance(uidnext_before, int) and isinstance(uidnext_after, int) and uidnext_after > uidnext_before:
            # Only scan a bounded tail window to avoid huge fetches under concurrency.
            start = max(uidnext_before, uidnext_after - 50)
            cand_uids = list(range(start, uidnext_after))
        else:
            try:
                all_uids = client.search(["ALL"])
            except Exception:
                all_uids = []
            if all_uids:
                tail = all_uids[-200:] if len(all_uids) > 200 else all_uids
                cand_uids = [int(u) for u in tail if isinstance(u, int)]
        if not cand_uids:
            return []
        try:
            data = client.fetch(cand_uids, [b"BODY.PEEK[HEADER]"])
        except Exception:
            return []
        hits: list[int] = []
        for uid in cand_uids:
            try:
                raw_hdr = data[uid].get(b"BODY[HEADER]") or data[uid].get(b"BODY[HEADER.FIELDS (MESSAGE-ID)]")
                if not raw_hdr:
                    raw_hdr = data[uid].get(b"BODY[]")  # type: ignore[assignment]
                if not raw_hdr:
                    continue
                hdr = BytesParser(policy=policy.default).parsebytes(raw_hdr)
                if str(hdr.get("Message-ID") or "").strip() in {mid, mid.strip("<>")}:
                    hits.append(int(uid))
            except Exception:
                continue
        return sorted(set(hits))

    # Fallbacks: search (may lag), then bounded fetch of recent headers.
    for _ in range(5):
        hits = _try_locate_by_search()
        if hits:
            return mid, hits[-1]
        time.sleep(0.4)

    hits = _try_locate_by_fetch_recent()
    if hits:
        return mid, hits[-1]

    raise RuntimeError(f"Could not locate appended test mail in folder={folder} (mid={mid})")


def _exists_by_uid(client, folder: str, uid: int) -> bool:
    folder = _normalize_qq_folder(folder)
    client.select_folder(folder, readonly=True)
    try:
        data = client.fetch([uid], [b"FLAGS"])
    except Exception:
        return False
    return isinstance(data, dict) and uid in data


def _search_by_message_id(client, folder: str, message_id: str) -> list[int]:
    folder = _normalize_qq_folder(folder)
    client.select_folder(folder, readonly=True)
    mids = [message_id, message_id.strip("<>")]
    for m in mids:
        try:
            uids = client.search(["HEADER", "Message-ID", m])
        except Exception:
            continue
        if uids:
            return [int(u) for u in uids if isinstance(u, int)]
    return []


def _find_by_message_id_fetch_tail(client, folder: str, message_id: str, max_scan: int = 200) -> list[int]:
    folder = _normalize_qq_folder(folder)
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
            got2 = got.strip("<>")
            if got in want or got2 in want:
                hits.append(int(uid))
        except Exception:
            continue
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description="Real IMAP smoke test: move a message into Trash.")
    parser.add_argument(
        "--config",
        default=str(REPO_ROOT / "config.json"),
        help="Path to config.json (default: repo root config.json)",
    )
    parser.add_argument(
        "--source-folder",
        default="",
        help="Source folder to operate on. Default: translate.folders[0] from config.",
    )
    parser.add_argument(
        "--trash-folder",
        default="",
        help="Trash folder. Default: translate.trash_folder from config.",
    )
    parser.add_argument(
        "--uid",
        type=int,
        default=0,
        help="Move an existing message UID (DANGEROUS). If omitted, a test mail is appended then moved.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required when using --uid (acknowledge this will delete the source message).",
    )
    parser.add_argument(
        "--force-copy",
        action="store_true",
        help="Do not use IMAP MOVE even if supported; use COPY+DELETE instead.",
    )
    parser.add_argument(
        "--no-expunge",
        action="store_true",
        help="When using COPY+DELETE, skip EXPUNGE (message may remain \\Deleted).",
    )
    parser.add_argument(
        "--mark-seen-before-move",
        action="store_true",
        help="Mimic production flow: STORE \\Seen before moving.",
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

    translate_cfg = cfg.get("translate") or {}
    if not isinstance(translate_cfg, dict):
        translate_cfg = {}

    source_folder = str(args.source_folder or "").strip()
    if not source_folder:
        folders = translate_cfg.get("folders") or []
        if isinstance(folders, list) and folders:
            source_folder = str(folders[0] or "").strip()
    source_folder = _normalize_qq_folder(source_folder or "INBOX")

    trash_folder = str(args.trash_folder or "").strip() or str(translate_cfg.get("trash_folder") or "").strip()
    trash_folder = _normalize_qq_folder(trash_folder)
    if not trash_folder:
        raise ValueError("trash_folder is empty (pass --trash-folder or set translate.trash_folder)")

    if args.uid and not args.yes:
        raise SystemExit("--uid is destructive; re-run with --yes to confirm.")

    result: dict[str, Any] = {
        "source_folder": source_folder,
        "source_folder_esc": _esc(source_folder),
        "trash_folder": trash_folder,
        "trash_folder_esc": _esc(trash_folder),
        "mode": "existing_uid" if args.uid else "append_test_mail",
    }

    c = connect(host, email, password, port=port, ssl=ssl)
    try:
        # Ensure folders are selectable
        c.select_folder(source_folder, readonly=True)
        c.select_folder(trash_folder, readonly=True)

        if args.uid:
            uid = int(args.uid)
            result["uid"] = uid
            result["exists_in_source_before"] = _exists_by_uid(c, source_folder, uid)
            result["trash_hits_before"] = []
            mid = None
        else:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            subject = f"[imapTLDR3 test] move-to-trash {ts}"
            html = "<html><body><p>imapTLDR3 move-to-trash test mail.</p></body></html>"
            mid, uid = _append_test_message_and_get_uid(c, source_folder, subject, html, None)
            result["uid"] = uid
            result["message_id"] = mid
            result["message_id_esc"] = _esc(mid)
            result["exists_in_source_before"] = _exists_by_uid(c, source_folder, uid)
            result["trash_hits_before"] = _find_by_message_id_fetch_tail(c, trash_folder, mid)

        # Move using production helper, but allow forcing COPY+DELETE for diagnosis
        if args.mark_seen_before_move:
            c.select_folder(source_folder)
            c.add_flags([uid], [b"\\Seen"])
            result["marked_seen_before_move"] = True
        else:
            result["marked_seen_before_move"] = False

        if args.force_copy or args.no_expunge:
            # Inline a minimal COPY+DELETE flow to keep behavior explicit in tests
            c.select_folder(source_folder)
            c.copy([uid], trash_folder)
            c.add_flags([uid], [b"\\Deleted"])
            if not args.no_expunge:
                try:
                    c.expunge()
                except Exception:
                    pass
            result["move_method"] = "copy+delete" + ("(no_expunge)" if args.no_expunge else "")
        else:
            dst = move_to_trash(c, source_folder, uid, trash_folder)
            result["move_method"] = "move_to_trash"
            result["move_to_trash_return"] = dst

        # Verify after move
        result["exists_in_source_after"] = _exists_by_uid(c, source_folder, uid)
        if mid:
            # allow a brief server-side lag
            time.sleep(0.3)
            result["trash_hits_after"] = _find_by_message_id_fetch_tail(c, trash_folder, mid)
            if not result["trash_hits_after"] and trash_folder != "Deleted Messages":
                result["deleted_messages_hits_after"] = _find_by_message_id_fetch_tail(c, "Deleted Messages", mid)
        else:
            result["trash_hits_after"] = None

        print(json.dumps(result, ensure_ascii=True))
        return 0
    finally:
        try:
            c.logout()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

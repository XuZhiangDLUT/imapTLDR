import argparse
import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from imapclient import IMAPClient  # noqa: E402

from mailbot.config import load_config  # noqa: E402
from mailbot.imap_client import connect  # noqa: E402


@dataclass(frozen=True)
class Mailbox:
    name: str
    flags: tuple[str, ...]
    delimiter: str

    @property
    def name_esc(self) -> str:
        return _esc(self.name)

    @property
    def flags_norm(self) -> tuple[str, ...]:
        return tuple(_normalize_flag(f) for f in self.flags)

    @property
    def noselect(self) -> bool:
        return any(_normalize_flag(f).lower() == "\\noselect" for f in self.flags)


def _esc(value: str) -> str:
    return (value or "").encode("unicode_escape").decode("ascii")


def _to_str(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.decode("utf-8", errors="ignore")
    return str(value)


def _normalize_flag(value: str) -> str:
    s = _to_str(value).strip()
    # Be tolerant to servers returning flags without leading backslash.
    if not s:
        return s
    if s.startswith("\\"):
        return s
    if s.startswith("/"):
        return "\\" + s[1:]
    return s


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


def _first_present(mapping: dict[Any, Any], *keys: Any) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _get_folder_counts(client: IMAPClient, folder: str) -> dict[str, int | None]:
    # Prefer STATUS to avoid side effects; fallback to SELECT(READONLY) when needed.
    total = None
    unseen = None
    try:
        status = client.folder_status(folder, [b"MESSAGES", b"UNSEEN", b"UIDNEXT"])  # type: ignore[arg-type]
        if isinstance(status, dict):
            total = _coerce_int(_first_present(status, b"MESSAGES", "MESSAGES"))
            unseen = _coerce_int(_first_present(status, b"UNSEEN", "UNSEEN"))
            uidnext = _coerce_int(_first_present(status, b"UIDNEXT", "UIDNEXT"))
        else:
            uidnext = None
    except Exception:
        uidnext = None

    if total is None or unseen is None or uidnext is None:
        try:
            sel = client.select_folder(folder, readonly=True)
        except Exception:
            sel = None
        if isinstance(sel, dict):
            if total is None:
                total = _coerce_int(_first_present(sel, b"EXISTS", "EXISTS"))
            if unseen is None:
                unseen = _coerce_int(_first_present(sel, b"UNSEEN", "UNSEEN"))
            if uidnext is None:
                uidnext = _coerce_int(_first_present(sel, b"UIDNEXT", "UIDNEXT"))

    return {"total": total, "unseen": unseen, "uidnext": uidnext}


def _sample_tail_uids(
    client: IMAPClient,
    folder: str,
    *,
    limit: int = 3,
    window: int = 500,
) -> list[dict[str, Any]]:
    """
    Return a few UIDs near the end of the mailbox with minimal metadata (UID + RFC822.SIZE).
    Uses UIDNEXT windowed FETCH to avoid SEARCH ALL on huge folders.
    """
    counts = _get_folder_counts(client, folder)
    uidnext = counts.get("uidnext")
    if not isinstance(uidnext, int) or uidnext <= 1:
        uidnext = None

    client.select_folder(folder, readonly=True)

    if uidnext is not None:
        start = max(1, int(uidnext) - max(1, int(window)))
        end = int(uidnext) - 1
        if end >= start:
            try:
                data = client.fetch(f"{start}:{end}", [b"RFC822.SIZE"])
                uids = sorted(int(u) for u in data.keys() if isinstance(u, int))
                tail = uids[-limit:] if limit > 0 else []
                out = []
                for uid in tail:
                    try:
                        size = data[uid].get(b"RFC822.SIZE") or data[uid].get("RFC822.SIZE")
                    except Exception:
                        size = None
                    out.append({"uid": uid, "rfc822_size": _coerce_int(size)})
                return out
            except Exception:
                pass

    # Fallback: SEARCH ALL and take the last N.
    try:
        uids = client.search(["ALL"])
    except Exception:
        uids = []
    if not uids or limit <= 0:
        return []
    tail = [int(u) for u in (uids[-limit:] if len(uids) > limit else uids)]
    try:
        data = client.fetch(tail, [b"RFC822.SIZE"])
    except Exception:
        data = {}
    out = []
    for uid in tail:
        try:
            size = data.get(uid, {}).get(b"RFC822.SIZE") or data.get(uid, {}).get("RFC822.SIZE")
        except Exception:
            size = None
        out.append({"uid": uid, "rfc822_size": _coerce_int(size)})
    return out


def _list_mailboxes(client: IMAPClient) -> list[Mailbox]:
    boxes: list[Mailbox] = []
    for flags, delimiter, name in client.list_folders():
        flags_s = tuple(_to_str(f) for f in (flags or ()))
        boxes.append(Mailbox(name=_to_str(name), flags=flags_s, delimiter=_to_str(delimiter)))
    return boxes


def _keyword_hit(name: str, keywords: Sequence[str]) -> bool:
    n = (name or "").lower()
    for k in keywords:
        if not k:
            continue
        if k.lower() in n:
            return True
    return False


def _score_candidate(name: str, flags: Sequence[str], *, category: str) -> int:
    """
    Higher score = more likely the "system" mailbox for the category.
    """
    n = (name or "").strip()
    nl = n.lower()
    fn = [(_normalize_flag(f) or "").lower() for f in (flags or ())]

    if "\\noselect" in fn:
        return -10_000

    matched = False
    score = 0

    # Prefer SPECIAL-USE flags when available.
    special_flag = {
        "sent": "\\sent",
        "drafts": "\\drafts",
        "deleted": "\\trash",
        "junk": "\\junk",
    }.get(category)
    if special_flag and special_flag in fn:
        matched = True
        score += 10_000

    # Name heuristics.
    if category == "sent":
        if _keyword_hit(n, ["sent", "已发送", "已發送", "sent mail", "sent messages", "sent items", "发件箱", "發件箱"]):
            matched = True
            score += 500
    elif category == "drafts":
        if _keyword_hit(n, ["draft", "drafts", "草稿", "草稿箱"]):
            matched = True
            score += 500
    elif category == "deleted":
        if _keyword_hit(n, ["trash", "deleted", "已删除", "已刪除", "回收站", "bin", "deleted messages"]):
            matched = True
            score += 500
    elif category == "junk":
        if _keyword_hit(n, ["junk", "spam", "垃圾", "垃圾箱", "垃圾邮件", "廣告", "广告"]):
            matched = True
            score += 500
    elif category == "group":
        if _keyword_hit(n, ["群邮件", "群郵件", "群组", "群組", "group", "groups", "mailing list", "list"]):
            matched = True
            score += 300
    elif category == "starred":
        if _keyword_hit(n, ["星标", "星標", "starred", "flagged"]):
            matched = True
            score += 300

    # Prefer "exact-ish" matches.
    exactish = {
        "sent": {"sent", "sent mail", "sent messages", "sent items", "已发送", "已发送邮件"},
        "drafts": {"drafts", "draft", "草稿箱", "草稿"},
        "deleted": {"trash", "deleted messages", "deleted items", "已删除", "已删除邮件"},
        "junk": {"junk", "spam", "垃圾箱", "垃圾邮件"},
        "group": {"群邮件", "群组", "groups"},
        "starred": {"星标邮件", "starred", "flagged"},
    }.get(category, set())
    if nl in exactish:
        matched = True
        score += 200

    if not matched:
        return -1

    # Tie-breakers (only after we have a positive match).
    # Prefer top-level (avoid "其他文件夹/垃圾箱" etc) when user explicitly asked for system folders.
    if "/" not in n and "\\" not in n:
        score += 200
    if "其他文件夹" in n or "other" in nl:
        score -= 200

    # Avoid known "manual" folder path mentioned by the user.
    if category == "junk" and ("其他文件夹/垃圾箱" in n or "other folders/trash" in nl):
        score -= 5_000

    # Gmail namespace hint.
    if nl.startswith("[gmail]/") or nl.startswith("[google mail]/"):
        score += 50

    return score


def _pick_candidates(mailboxes: list[Mailbox], *, category: str, max_candidates: int = 5) -> list[Mailbox]:
    scored = sorted(
        mailboxes,
        key=lambda mb: _score_candidate(mb.name, mb.flags_norm, category=category),
        reverse=True,
    )
    # Keep only positive-scored results (unless nothing found).
    picked = []
    for mb in scored:
        if mb.noselect:
            continue
        s = _score_candidate(mb.name, mb.flags_norm, category=category)
        if s <= 0:
            continue
        picked.append(mb)
        if len(picked) >= max_candidates:
            break
    return picked


def _try_read_folder(client: IMAPClient, folder: str, *, sample: int, window: int) -> dict[str, Any]:
    row: dict[str, Any] = {"folder": folder, "folder_esc": _esc(folder)}
    try:
        client.select_folder(folder, readonly=True)
    except Exception as exc:
        row["ok"] = False
        row["error"] = str(exc)
        return row

    row["ok"] = True
    counts = _get_folder_counts(client, folder)
    row.update(
        {
            "total": counts.get("total"),
            "unseen": counts.get("unseen"),
            "uidnext": counts.get("uidnext"),
        }
    )

    # FLAGGED messages are what most clients show as "starred".
    try:
        flagged_uids = client.search(["FLAGGED"])
        row["flagged_count_in_folder"] = len(flagged_uids or [])
    except Exception as exc:
        row["flagged_count_in_folder"] = None
        row["flagged_error"] = str(exc)

    # Sample a few messages to confirm FETCH works (metadata only).
    try:
        row["sample_tail"] = _sample_tail_uids(client, folder, limit=sample, window=window)
    except Exception as exc:
        row["sample_tail"] = []
        row["sample_error"] = str(exc)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="IMAP test: detect and read special/system folders safely (readonly).")
    parser.add_argument(
        "--config",
        default=str(REPO_ROOT / "config.json"),
        help="Path to config.json (default: repo root config.json)",
    )
    parser.add_argument("--sample", type=int, default=3, help="How many tail messages to sample per folder.")
    parser.add_argument(
        "--window",
        type=int,
        default=500,
        help="UIDNEXT tail window for sampling (avoid SEARCH ALL on huge folders).",
    )
    parser.add_argument(
        "--list-all",
        action="store_true",
        help="Print all mailboxes returned by LIST (names + flags).",
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

    print(
        json.dumps(
            {
                "action": "connect",
                "server": host,
                "port": port,
                "ssl": ssl,
                "email_masked": _mask_email(email),
            },
            ensure_ascii=True,
        )
    )

    client: IMAPClient = connect(host, email, password, port=port, ssl=ssl)
    try:
        try:
            caps = client.capabilities() or []
            caps_s = sorted(_to_str(c) for c in caps)
        except Exception as exc:
            caps_s = []
            print(json.dumps({"action": "capabilities", "ok": False, "error": str(exc)}, ensure_ascii=True))
        else:
            print(
                json.dumps(
                    {
                        "action": "capabilities",
                        "ok": True,
                        "has_special_use": any("SPECIAL-USE" in c.upper() for c in caps_s),
                        "has_xlist": any("XLIST" in c.upper() for c in caps_s),
                        "caps": caps_s,
                    },
                    ensure_ascii=True,
                )
            )

        mailboxes = _list_mailboxes(client)
        if args.list_all:
            for mb in mailboxes:
                print(
                    json.dumps(
                        {
                            "action": "mailbox",
                            "name": mb.name,
                            "name_esc": mb.name_esc,
                            "delimiter": mb.delimiter,
                            "flags": list(mb.flags_norm),
                            "noselect": mb.noselect,
                        },
                        ensure_ascii=True,
                    )
                )

        targets = [
            ("starred", "星标邮件(FLAGGED)"),
            ("group", "群邮件"),
            ("sent", "已发送"),
            ("drafts", "草稿箱"),
            ("deleted", "已删除(Trash)"),
            ("junk", "垃圾箱(Junk/Spam)"),
        ]

        results: list[dict[str, Any]] = []
        for key, label in targets:
            cands = _pick_candidates(mailboxes, category=key, max_candidates=5)
            print(
                json.dumps(
                    {
                        "action": "candidates",
                        "category": key,
                        "label": label,
                        "count": len(cands),
                        "items": [
                            {
                                "name": mb.name,
                                "name_esc": mb.name_esc,
                                "flags": list(mb.flags_norm),
                                "score": _score_candidate(mb.name, mb.flags_norm, category=key),
                            }
                            for mb in cands
                        ],
                    },
                    ensure_ascii=True,
                )
            )

            if cands:
                # Read the best candidate.
                best = cands[0]
                results.append(
                    {
                        "category": key,
                        "label": label,
                        "picked": best.name,
                        "picked_esc": best.name_esc,
                        "picked_flags": list(best.flags_norm),
                        "read": _try_read_folder(client, best.name, sample=args.sample, window=args.window),
                    }
                )
            else:
                # Starred can still be tested via FLAGGED search in INBOX.
                if key == "starred":
                    results.append(
                        {
                            "category": key,
                            "label": label,
                            "picked": "INBOX",
                            "picked_esc": _esc("INBOX"),
                            "picked_flags": [],
                            "read": _try_read_folder(client, "INBOX", sample=args.sample, window=args.window),
                        }
                    )
                else:
                    results.append({"category": key, "label": label, "picked": None, "read": None})

        for row in results:
            print(json.dumps({"action": "result", **row}, ensure_ascii=True))

        return 0
    finally:
        try:
            client.logout()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

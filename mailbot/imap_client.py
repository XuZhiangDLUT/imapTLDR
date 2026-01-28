from imapclient import IMAPClient
from email.parser import BytesParser
from email import policy
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any, Iterable, Sequence
from itertools import islice

def connect(host: str, user: str, password: str, port: int = 993, ssl: bool = True) -> IMAPClient:
    client = IMAPClient(host, port=port, ssl=ssl)
    client.login(user, password)
    # prime capabilities to avoid None during literal handling
    try:
        _ = client.capabilities()
    except Exception:
        pass
    return client


def search_unseen_without_prefix(
    client: IMAPClient,
    folder: str,
    exclude_prefixes: Iterable[str] | None = None,
    keywords: list[str] | None = None,
    exclude_auto_generated: bool = True,
    robust: bool = False,
    fetch_chunk: int = 500,
) -> list[int]:
    client.select_folder(folder)
    if robust:
        return list_unseen_robust(client, folder, exclude_auto_generated=exclude_auto_generated, fetch_chunk=fetch_chunk)
    # Avoid non-ASCII in SEARCH to keep QQ IMAP happy: fetch UNSEEN only, filter client-side
    crit: list[str] = ["UNSEEN"]
    # Try server-side exclusion of auto-generated to reduce noise (ASCII-only)
    if exclude_auto_generated:
        try:
            return client.search(["UNSEEN", "NOT", "HEADER", "Auto-Submitted", "auto-generated"])  # type: ignore
        except Exception:
            pass
    uids = client.search(crit)
    if exclude_auto_generated and uids:
        # client-side filter by header
        try:
            data = client.fetch(uids, [b'BODY.PEEK[HEADER]'])
            kept = []
            for uid in uids:
                try:
                    hdr = BytesParser(policy=policy.default).parsebytes(data[uid][b'BODY[HEADER]'])
                    if str(hdr.get('Auto-Submitted','') or '').lower().strip() == 'auto-generated':
                        continue
                    kept.append(uid)
                except Exception:
                    kept.append(uid)
            return kept
        except Exception:
            return uids
    return uids


def fetch_raw(client: IMAPClient, uid: int) -> bytes:
    data = client.fetch([uid], [b"BODY.PEEK[]"])  # no \Seen side effect
    return data[uid][b"BODY[]"]


def parse_message(raw_bytes: bytes):
    return BytesParser(policy=policy.default).parsebytes(raw_bytes)


def pick_html_or_text(msg) -> tuple[str, str]:
    html_part = None
    text_part = None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/html" and html_part is None:
                html_part = part.get_content()
            elif ctype == "text/plain" and text_part is None:
                text_part = part.get_content()
    else:
        ctype = msg.get_content_type()
        if ctype == "text/html":
            html_part = msg.get_content()
        else:
            text_part = msg.get_content()
    return html_part, text_part


def build_email(subject: str, from_addr: str, to_addr: str, html: str | None, text: str | None, in_reply_to: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
        msg["X-Linked-Message-Id"] = in_reply_to
    # Always set a new Message-ID
    msg["Message-ID"] = make_msgid()
    if html and text:
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
    elif html:
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(text or "")
    return msg


def ensure_folder(client: IMAPClient, folder: str) -> str:
    try:
        client.select_folder(folder)
        return folder
    except Exception:
        return 'INBOX'


import logging

logger = logging.getLogger("mailbot")

def append_unseen(client: IMAPClient, folder: str, msg: EmailMessage):
    folder = ensure_folder(client, folder)
    # Append without \Seen flag
    mid = str(msg.get('Message-ID', '') or '')
    subj = str(msg.get('Subject', '') or '')

    # Try to capture UIDNEXT before APPEND so we can later bound the new UID.
    uidnext_before: int | None = None
    try:
        status_before = client.folder_status(folder, [b'UIDNEXT'])  # type: ignore[arg-type]
        if isinstance(status_before, dict):
            nxt = status_before.get(b'UIDNEXT') or status_before.get('UIDNEXT')
            if isinstance(nxt, int):
                uidnext_before = nxt
    except Exception:
        uidnext_before = None

    client.append(folder, msg.as_bytes(), flags=())

    # Capture UIDNEXT after APPEND to detect simple, non-concurrent cases.
    uidnext_after: int | None = None
    try:
        status_after = client.folder_status(folder, [b'UIDNEXT'])  # type: ignore[arg-type]
        if isinstance(status_after, dict):
            nxt = status_after.get(b'UIDNEXT') or status_after.get('UIDNEXT')
            if isinstance(nxt, int):
                uidnext_after = nxt
    except Exception:
        uidnext_after = None

    # Enforce UNSEEN for the newly appended message (best-effort, with multiple fallbacks)
    try:
        client.select_folder(folder)

        # Fast path: if UIDNEXT increased by exactly 1, the new UID is uidnext_before.
        if (
            isinstance(uidnext_before, int)
            and isinstance(uidnext_after, int)
            and uidnext_after == uidnext_before + 1
        ):
            try:
                client.remove_flags([uidnext_before], [b'\\Seen'])
                logger.info(
                    f"Enforce UNSEEN on appended mail via UIDNEXT: folder={folder}, uid={uidnext_before}"
                )
                return
            except Exception as e_fast:
                logger.info(f"UNSEEN enforcement via UIDNEXT failed: {e_fast}")

        candidates: list[int] = []
        # When we have UIDNEXT from before APPEND, new messages must have UID >= this value.
        uid_lower_bound: int | None = uidnext_before if isinstance(uidnext_before, int) else None

        # 1) Prefer exact Message-ID matches; they are globally unique and safe.
        if mid:
            mids = [mid, mid.strip('<>')]
            for m in mids:
                try:
                    uids = client.search(['HEADER', 'Message-ID', m])
                except Exception:
                    continue
                if not uids:
                    continue
                if uid_lower_bound is not None:
                    uids = [u for u in uids if isinstance(u, int) and u >= uid_lower_bound]
                if uids:
                    candidates.extend(uids)

        # 2) Fallback: narrow by Auto-Submitted header + Subject, but only for recent UIDs.
        if not candidates and subj and uid_lower_bound is not None:
            try:
                auto = client.search(['HEADER', 'Auto-Submitted', 'auto-generated'])
            except Exception:
                auto = []
            if auto and uid_lower_bound is not None:
                auto = [u for u in auto if isinstance(u, int) and u >= uid_lower_bound]
            pool = auto[-50:] if len(auto) > 50 else auto
            if pool:
                data = client.fetch(pool, [b'BODY.PEEK[HEADER]'])
                for uid in pool:
                    try:
                        hdr = BytesParser(policy=policy.default).parsebytes(data[uid][b'BODY[HEADER]'])
                        if str(hdr.get('Subject', '') or '') == subj:
                            candidates.append(uid)
                    except Exception:
                        continue
            # 3) Last resort: SUBJECT search limited to recent UIDs only.
            if not candidates:
                try:
                    by_sub = client.search(['SUBJECT', subj])
                except Exception:
                    by_sub = []
                if by_sub and uid_lower_bound is not None:
                    by_sub = [u for u in by_sub if isinstance(u, int) and u >= uid_lower_bound]
                if by_sub:
                    # last one most likely the appended
                    candidates.extend(by_sub[-1:])

        # De-dup and select the most recent UID only to avoid toggling older items
        if candidates:
            try:
                uniq = sorted({int(u) for u in candidates})
            except Exception:
                uniq = [int(candidates[-1])] if candidates else []
            target = [uniq[-1]] if uniq else []
            if target:
                client.remove_flags(target, [b'\\Seen'])
                logger.info(
                    f"Enforce UNSEEN on appended mail: folder={folder}, uid={target[0]}"
                )
        else:
            logger.info(
                f"Could not locate appended mail for UNSEEN enforcement: folder={folder}, subject={subj}"
            )
    except Exception as e:
        # ignore enforcement errors, log for diagnosis
        logger.info(f"UNSEEN enforcement skipped due to error: {e}")


def mark_seen(client: IMAPClient, folder: str, uid: int):
    client.select_folder(folder)
    client.add_flags([uid], [b"\\Seen"])  # make original read


def mark_unseen(client: IMAPClient, folder: str, uid: int):
    client.select_folder(folder)
    client.remove_flags([uid], [b"\\Seen"])  # ensure unread


def delete_message(client: IMAPClient, folder: str, uid: int, expunge: bool = True):
    client.select_folder(folder)
    client.add_flags([uid], [b"\\Deleted"])
    if expunge:
        try:
            client.expunge()
        except Exception:
            pass


def _uid_exists_in_selected_folder(client: IMAPClient, uid: int) -> bool:
    try:
        data = client.fetch([uid], [b"FLAGS"])
    except Exception:
        return False
    try:
        return isinstance(data, dict) and uid in data
    except Exception:
        return False


def move_message(client: IMAPClient, src_folder: str, uid: int, dst_folder: str):
    dst = str(dst_folder or "").strip()
    if not dst:
        raise ValueError("dst_folder is empty")

    client.select_folder(src_folder)

    # Prefer IMAP MOVE when supported by the server/client
    move_fn = getattr(client, "move", None)
    if callable(move_fn):
        try:
            move_fn([uid], dst)
            return
        except Exception:
            pass

    # Fallback: COPY + \Deleted + EXPUNGE
    try:
        client.copy([uid], dst)
    except Exception as e_copy:
        # QQ IMAP: "copy failed: Mails not exist!" typically means the UID is not present
        # in the currently selected folder. Treat as a no-op if it is already gone.
        if "Mails not exist" in str(e_copy):
            try:
                client.select_folder(src_folder)
            except Exception:
                pass
            if not _uid_exists_in_selected_folder(client, uid):
                logger.info(
                    f"move_message: uid missing in src folder, treat as already moved/deleted: folder={src_folder}, uid={uid}"
                )
                return
            # retry once after NOOP, some servers may be briefly inconsistent
            try:
                client.noop()
            except Exception:
                pass
            client.copy([uid], dst)
        else:
            raise

    try:
        client.add_flags([uid], [b"\\Deleted"])
    except Exception as e_del:
        if "Mails not exist" in str(e_del):
            try:
                client.select_folder(src_folder)
            except Exception:
                pass
            if not _uid_exists_in_selected_folder(client, uid):
                logger.info(
                    f"move_message: uid missing before delete, treat as already moved/deleted: folder={src_folder}, uid={uid}"
                )
                return
        raise
    try:
        # Prefer UID EXPUNGE when supported (limits side effects), fall back to full EXPUNGE.
        uid_expunge = getattr(client, "uid_expunge", None)
        if callable(uid_expunge):
            try:
                uid_expunge([uid])
                return
            except Exception:
                pass
        client.expunge()
    except Exception:
        pass


def move_to_trash(client: IMAPClient, src_folder: str, uid: int, trash_folder: str) -> str:
    dst = str(trash_folder or "").strip()
    if not dst:
        raise ValueError("translate.trash_folder is empty")
    move_message(client, src_folder, uid, dst)
    return dst


def _imap_to_str(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.decode("utf-8", errors="ignore")
    return str(value)


def _normalize_imap_flag(value: str) -> str:
    s = _imap_to_str(value).strip()
    if not s:
        return s
    if s.startswith("\\"):
        return s
    if s.startswith("/"):
        return "\\" + s[1:]
    return s


def _list_folders_prefer_xlist(client: IMAPClient) -> list[tuple[list[str], str, str]]:
    raw = None
    xlist_fn = getattr(client, "xlist_folders", None)
    if callable(xlist_fn):
        try:
            raw = xlist_fn()
        except Exception:
            raw = None
    if raw is None:
        raw = client.list_folders()
    out: list[tuple[list[str], str, str]] = []
    for flags, delim, name in raw:
        out.append(
            (
                [_imap_to_str(f) for f in (flags or ())],
                _imap_to_str(delim),
                _imap_to_str(name),
            )
        )
    return out


def find_system_junk_folder(client: IMAPClient) -> str | None:
    """
    Best-effort detection of the system Junk/Spam mailbox.

    Preference order:
    1) XLIST/SPECIAL-USE flags (\\Junk / \\Spam)
    2) Common names ("Junk", "Spam", "Junk E-mail", etc.)
    Tie-breakers prefer top-level folders and avoid known QQ manual folders like "其他文件夹/垃圾箱".
    """
    candidates: list[tuple[int, str]] = []

    for flags, _delim, name in _list_folders_prefer_xlist(client):
        n = str(name or "").strip()
        if not n:
            continue

        fl = [(_normalize_imap_flag(f) or "").lower() for f in (flags or [])]
        if "\\noselect" in fl:
            continue

        nl = n.lower()
        score = 0

        # Strongest signal: special-use flags (via XLIST or SPECIAL-USE).
        if "\\junk" in fl:
            score += 10_000
        if "\\spam" in fl:
            score += 9_000

        # Common names.
        if nl == "junk":
            score += 800
        if nl == "spam":
            score += 700
        if "junk" in nl:
            score += 500
        if "spam" in nl:
            score += 450
        if any(k in n for k in ["垃圾", "廣告", "广告", "垃圾箱", "垃圾邮件"]):
            score += 400

        # Prefer top-level system folders.
        if "/" not in n and "\\" not in n:
            score += 200
        if "其他文件夹" in n or "other" in nl:
            score -= 200

        # Explicitly avoid a known user-created folder pattern for QQ.
        if "其他文件夹/垃圾箱" in n:
            score -= 5_000

        if score > 0:
            candidates.append((score, n))

    candidates.sort(key=lambda x: x[0], reverse=True)

    for _score, folder in candidates:
        try:
            client.select_folder(folder, readonly=True)
            return folder
        except Exception:
            continue

    return None


def move_to_junk(client: IMAPClient, src_folder: str, uid: int) -> str:
    dst = find_system_junk_folder(client)
    if not dst:
        raise ValueError("Could not locate system Junk/Spam folder on this IMAP server")
    move_message(client, src_folder, uid, dst)
    return dst


def list_unseen(client: IMAPClient, folder: str, exclude_auto_generated: bool = False) -> list[int]:
    client.select_folder(folder)
    if exclude_auto_generated:
        try:
            return client.search(["UNSEEN", "NOT", "HEADER", "Auto-Submitted", "auto-generated"])  # type: ignore
        except Exception:
            pass
    uids = client.search(["UNSEEN"])
    if exclude_auto_generated and uids:
        try:
            data = client.fetch(uids, [b'BODY.PEEK[HEADER]'])
            kept = []
            for uid in uids:
                try:
                    hdr = BytesParser(policy=policy.default).parsebytes(data[uid][b'BODY[HEADER]'])
                    if str(hdr.get('Auto-Submitted','') or '').lower().strip() == 'auto-generated':
                        continue
                    kept.append(uid)
                except Exception:
                    kept.append(uid)
            return kept
        except Exception:
            return uids
    return uids

def has_linked_reply(client: IMAPClient, folder: str, orig_msgid: str, prefix: str) -> bool:
    client.select_folder(folder)
    try:
        uids = client.search(['ALL'])
    except Exception:
        return False
    if not uids:
        return False
    tail = uids[-200:] if len(uids) > 200 else uids
    data = client.fetch(tail, [b'BODY.PEEK[HEADER]'])
    for uid in data:
        hdr = BytesParser(policy=policy.default).parsebytes(data[uid][b'BODY[HEADER]'])
        sub = str(hdr.get('Subject','') or '')
        if sub.startswith(prefix) and hdr.get('X-Linked-Message-Id','') == orig_msgid:
            return True
    return False


def list_unseen_robust(client: IMAPClient, folder: str, exclude_auto_generated: bool = True, fetch_chunk: int = 500) -> list[int]:
    """Robust UNSEEN enumeration that avoids SEARCH result truncation.

    Strategy:
    - Use UID ranges derived from UIDNEXT to iterate across the entire mailbox.
    - Fetch FLAGS in chunks and filter client-side for UNSEEN.
    - Optionally filter out auto-generated messages by inspecting headers.
    """
    client.select_folder(folder)

    # Determine UID upper bound without relying on SEARCH
    max_uid: int | None = None
    try:
        status = client.folder_status(folder, [b'UIDNEXT'])  # type: ignore[arg-type]
        if isinstance(status, dict):
            nxt = status.get(b'UIDNEXT') or status.get('UIDNEXT')
            if isinstance(nxt, int):
                # UIDNEXT is the next UID to be assigned, so max UID is UIDNEXT-1
                max_uid = max(0, nxt - 1)
    except Exception:
        max_uid = None

    unseen: list[int] = []
    step = max(1, int(fetch_chunk))

    if max_uid and max_uid > 0:
        # Walk through the full UID space using UID range FETCH
        start = 1
        while start <= max_uid:
            end = min(start + step - 1, max_uid)
            uid_range = f"{start}:{end}"
            try:
                data = client.fetch(uid_range, [b'FLAGS'])
            except Exception:
                # If range fetch fails (server quirk), try to fall back to SEARCH for this window
                try:
                    # Attempt a bounded SEARCH using SINCE/BEFORE isn't reliable without dates; skip to next window
                    data = None
                except Exception:
                    data = None
            if isinstance(data, dict) and data:
                for uid, info in data.items():
                    try:
                        flags = info.get(b'FLAGS') or info.get('FLAGS') or ()
                        if b'\\Seen' not in flags:
                            unseen.append(int(uid))
                    except Exception:
                        unseen.append(int(uid))
            start = end + 1
    else:
        # Fallback: rely on SEARCH ALL (older servers) and chunk-FETCH FLAGS
        try:
            all_uids = client.search(['ALL'])
        except Exception:
            all_uids = []
        for i in range(0, len(all_uids), step):
            chunk = all_uids[i:i+step]
            try:
                data = client.fetch(chunk, [b'FLAGS'])
            except Exception:
                continue
            for uid in chunk:
                try:
                    flags = data[uid][b'FLAGS']
                    if b'\\Seen' not in flags:
                        unseen.append(uid)
                except Exception:
                    unseen.append(uid)

    if exclude_auto_generated and unseen:
        kept: list[int] = []
        for i in range(0, len(unseen), step):
            chunk = unseen[i:i+step]
            try:
                data = client.fetch(chunk, [b'BODY.PEEK[HEADER]'])
            except Exception:
                kept.extend(chunk)
                continue
            for uid in chunk:
                try:
                    hdr = BytesParser(policy=policy.default).parsebytes(data[uid][b'BODY[HEADER]'])
                    if str(hdr.get('Auto-Submitted','') or '').lower().strip() == 'auto-generated':
                        continue
                    kept.append(uid)
                except Exception:
                    kept.append(uid)
        unseen = kept
    return unseen

from imapclient import IMAPClient
from email.parser import BytesParser
from email import policy
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Iterable
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
    client.append(folder, msg.as_bytes(), flags=())
    # Enforce UNSEEN for the newly appended message (best-effort, with multiple fallbacks)
    try:
        client.select_folder(folder)
        candidates: list[int] = []
        if mid:
            mids = [mid, mid.strip('<>')]
            for m in mids:
                try:
                    u = client.search(['HEADER', 'Message-ID', m])
                    if u:
                        candidates.extend(u)
                except Exception:
                    pass
        if not candidates and subj:
            # Prefer auto-generated header to narrow down
            try:
                auto = client.search(['HEADER', 'Auto-Submitted', 'auto-generated'])
            except Exception:
                auto = []
            pool = auto[-50:] if len(auto) > 50 else auto
            if pool:
                data = client.fetch(pool, [b'BODY.PEEK[HEADER]'])
                for uid in pool:
                    try:
                        hdr = BytesParser(policy=policy.default).parsebytes(data[uid][b'BODY[HEADER]'])
                        if str(hdr.get('Subject','') or '') == subj:
                            candidates.append(uid)
                    except Exception:
                        continue
            # Fallback to SUBJECT search if still not found
            if not candidates:
                try:
                    by_sub = client.search(['SUBJECT', subj])
                    if by_sub:
                        candidates.extend(by_sub[-1:])  # last one most likely the appended
                except Exception:
                    pass
        if candidates:
            client.remove_flags(candidates, [b'\\Seen'])
            logger.info(f"Enforce UNSEEN on appended mail: folder={folder}, uids={candidates}")
        else:
            logger.info(f"Could not locate appended mail for UNSEEN enforcement: folder={folder}, subject={subj}")
    except Exception as e:
        # ignore enforcement errors, log for diagnosis
        logger.info(f"UNSEEN enforcement skipped due to error: {e}")


def mark_seen(client: IMAPClient, folder: str, uid: int):
    client.select_folder(folder)
    client.add_flags([uid], [b"\\Seen"])  # make original read


def mark_unseen(client: IMAPClient, folder: str, uid: int):
    client.select_folder(folder)
    client.remove_flags([uid], [b"\\Seen"])  # ensure unread


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
    """Robust UNSEEN enumeration by fetching FLAGS over ALL UIDs in chunks.
    Works around server SEARCH limits by client-side filtering.
    """
    client.select_folder(folder)
    try:
        all_uids = client.search(['ALL'])
    except Exception:
        return []
    if not all_uids:
        return []
    unseen: list[int] = []
    step = max(1, int(fetch_chunk))
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
        step = max(1, int(fetch_chunk))
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
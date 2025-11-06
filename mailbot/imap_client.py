from imapclient import IMAPClient
from email.parser import BytesParser
from email import policy
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Iterable


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
) -> list[int]:
    client.select_folder(folder)
    # Avoid non-ASCII in SEARCH to keep QQ IMAP happy: fetch UNSEEN only, filter client-side
    crit: list[str] = ["UNSEEN"]
    # Keywords omitted in SEARCH to avoid UTF-8 literals; can be filtered client-side if needed
    return client.search(crit)


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


def append_unseen(client: IMAPClient, folder: str, msg: EmailMessage):
    folder = ensure_folder(client, folder)
    client.append(folder, msg.as_bytes(), flags=())


def mark_seen(client: IMAPClient, folder: str, uid: int):
    client.select_folder(folder)
    client.add_flags([uid], [b"\\Seen"])  # make original read


def list_unseen(client: IMAPClient, folder: str) -> list[int]:
    client.select_folder(folder)
    return client.search(["UNSEEN"])


def has_linked_reply(client: IMAPClient, folder: str, orig_msgid: str, prefix: str) -> bool:
    client.select_folder(folder)
    try:
        uids = client.search(['ALL'])
    except Exception:
        return False
    if not uids:
        return False
    tail = uids[-100:] if len(uids) > 100 else uids
    data = client.fetch(tail, [b'BODY.PEEK[HEADER]'])
    for uid in data:
        hdr = BytesParser(policy=policy.default).parsebytes(data[uid][b'BODY[HEADER]'])
        sub = str(hdr.get('Subject','') or '')
        if sub.startswith(prefix) and hdr.get('X-Linked-Message-Id','') == orig_msgid:
            return True
    return False
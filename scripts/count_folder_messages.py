import json
import pathlib
import sys
from typing import Any, Iterable, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from imapclient import IMAPClient

from mailbot import imap_client


FolderEntry = Tuple[Iterable[str], str, str]
CountRow = Tuple[str, int | None, int | None, int | None, str | None]


def load_config(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def get_imap_settings(config: dict[str, Any]) -> dict[str, Any]:
    imap_conf = config.get("imap")
    if not isinstance(imap_conf, dict):
        raise ValueError("Missing 'imap' section in config.json")
    required = ["server", "email", "password"]
    for key in required:
        if key not in imap_conf:
            raise ValueError(f"Missing imap.{key} in config.json")
    return imap_conf


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _format_value(value: int | None) -> str:
    return f"{value}" if value is not None else "-"


def _first_present(mapping: dict[Any, Any], *keys: Any) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def fetch_folder_counts(client: IMAPClient) -> list[CountRow]:
    rows: list[CountRow] = []
    folders: list[FolderEntry] = client.list_folders()
    for _flags, _delimiter, name in folders:
        try:
            status = client.folder_status(name, [b"MESSAGES", b"UNSEEN"])
            total = _coerce_int(_first_present(status, b"MESSAGES", "MESSAGES"))
            unread = _coerce_int(_first_present(status, b"UNSEEN", "UNSEEN"))

            if total is None or unread is None:
                try:
                    select_info = client.select_folder(name, readonly=True)
                except Exception:
                    select_info = None
                if select_info:
                    if total is None:
                        total = _coerce_int(_first_present(select_info, b"EXISTS", "EXISTS"))
                    if unread is None:
                        unread = _coerce_int(_first_present(select_info, b"UNSEEN", "UNSEEN"))

            read = total - unread if (total is not None and unread is not None) else None
            rows.append((name, total, unread, read, None))
        except Exception as exc:
            rows.append((name, None, None, None, str(exc)))
    return rows


def main() -> None:
    config = load_config(REPO_ROOT / "config.json")
    imap_settings = get_imap_settings(config)

    server = str(imap_settings.get("server"))
    email = str(imap_settings.get("email"))
    password = str(imap_settings.get("password"))
    port = int(imap_settings.get("port", 993))
    ssl = bool(imap_settings.get("ssl", True))

    client = imap_client.connect(server, email, password, port=port, ssl=ssl)
    try:
        rows = fetch_folder_counts(client)
    finally:
        try:
            client.logout()
        except Exception:
            pass

    if not rows:
        print("No folders returned by the IMAP server.")
        return

    name_width = max(10, max(len(name) for name, *_ in rows))
    header = f"{'Folder':<{name_width}}  {'Total':>10}  {'Unread':>10}  {'Read':>10}  Status"
    print(header)
    print("-" * len(header))
    for name, total, unread, read, error in rows:
        if error:
            print(f"{name:<{name_width}}  {'-':>10}  {'-':>10}  {'-':>10}  ERROR: {error}")
        else:
            print(
                f"{name:<{name_width}}  {_format_value(total):>10}  {_format_value(unread):>10}  {_format_value(read):>10}  OK"
            )


if __name__ == "__main__":
    main()

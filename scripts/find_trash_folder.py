import json
import pathlib
import sys
from typing import Any, Iterable, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from imapclient import IMAPClient  # noqa: E402

from mailbot.imap_client import connect  # noqa: E402


FolderEntry = Tuple[Iterable[str], str, str]


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


def main() -> int:
    cfg = load_config(REPO_ROOT / "config.json")
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
    trash_folder = str(translate_cfg.get("trash_folder") or "").strip()

    print(f"准备连接 IMAP: {host}:{port} ssl={ssl} email={_mask_email(email)}")
    print(f"配置 trash_folder: {trash_folder or '(未配置)'}")

    client: IMAPClient = connect(host, email, password, port=port, ssl=ssl)
    try:
        folders: list[FolderEntry] = client.list_folders()
        print(f"LIST 返回文件夹数量: {len(folders)}")

        def _to_str(value: Any) -> str:
            if isinstance(value, (bytes, bytearray)):
                try:
                    return value.decode("utf-8")
                except Exception:
                    return value.decode("utf-8", errors="ignore")
            return str(value)

        def _esc(s: str) -> str:
            return (s or "").encode("unicode_escape").decode("ascii")

        keywords = ("垃圾箱", "垃圾", "删除", "trash", "deleted")
        candidates = []
        for flags, delim, name in folders:
            n = _to_str(name)
            nl = n.lower()
            if ("垃圾" in n) or ("删除" in n) or ("trash" in nl) or ("deleted" in nl):
                candidates.append(
                    (
                        n,
                        [_to_str(f) for f in (flags or ())],
                        _to_str(delim),
                    )
                )

        selectable = []
        for n, flags, delim in candidates:
            ok = True
            err = ""
            try:
                client.select_folder(n, readonly=True)
            except Exception as exc:
                ok = False
                err = str(exc)
            row = {
                "name": n,
                "name_esc": _esc(n),
                "selectable": ok,
                "flags": flags,
                "delimiter": delim,
            }
            if err:
                row["error"] = err
            print(json.dumps(row, ensure_ascii=True))
            if ok:
                selectable.append(n)

        if trash_folder:
            ok = True
            err = ""
            try:
                client.select_folder(trash_folder, readonly=True)
            except Exception as exc:
                ok = False
                err = str(exc)
            print(
                json.dumps(
                    {
                        "configured_trash_folder": trash_folder,
                        "configured_trash_folder_esc": _esc(trash_folder),
                        "selectable": ok,
                        "error": err or None,
                    },
                    ensure_ascii=True,
                )
            )

        if selectable:
            print("可选中的候选文件夹（建议将 translate.trash_folder 设为其中之一）：")
            for n in selectable:
                print(f"- {n}")
            return 0

        print("未发现可选中的垃圾箱/已删除候选文件夹。")
        return 2
    finally:
        try:
            client.logout()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

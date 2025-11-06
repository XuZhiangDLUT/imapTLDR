import sys
import json
from pathlib import Path

from mailbot.config import load_config
from mailbot.translate import translate_once
from mailbot.summarize import summarize_once
# second-phase jobs
from mailbot.jobs import translate_job, summarize_job


USAGE = """
Usage:
  python run.py translate [max]
  python run.py summarize [folder] [batch]
  python run.py smoke
  python run.py translate_job    # second-phase translate
  python run.py summarize_job    # second-phase summarize
"""


def main(argv):
    if len(argv) < 2:
        print(USAGE)
        return 1
    cmd = argv[1]
    cfg = load_config()

    if cmd == "translate":
        max_items = int(argv[2]) if len(argv) > 2 else 2
        res = translate_once(cfg, max_items=max_items)
        print(f"translate: {len(res) if res else 0} items processed")
        return 0

    if cmd == "summarize":
        folder = argv[2] if len(argv) > 2 else None
        batch = int(argv[3]) if len(argv) > 3 else 5
        cnt = summarize_once(cfg, folder=folder, batch=batch)
        print(f"summarize: {cnt or 0} items summarized")
        return 0

    if cmd == "smoke":
        # run a tiny end-to-end: translate 1 + summarize 1
        res = translate_once(cfg, max_items=1)
        cnt = summarize_once(cfg, batch=1)
        print(json.dumps({"translate": len(res or []), "summarize": cnt or 0}))
        return 0

    if cmd == "translate_job":
        translate_job(cfg)
        print("translate_job: done")
        return 0

    if cmd == "summarize_job":
        summarize_job(cfg)
        print("summarize_job: done")
        return 0

    print(USAGE)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

import sys

from mailbot.config import load_config
from mailbot.summarize import summarize_once
# second-phase job
from mailbot.jobs import summarize_job


USAGE = """
Usage:
  python run.py summarize [folder] [batch]
  python run.py summarize_job    # scheduled-style summarize
"""


def main(argv):
    if len(argv) < 2:
        print(USAGE)
        return 1
    cmd = argv[1]
    cfg = load_config()

    if cmd == "summarize":
        folder = argv[2] if len(argv) > 2 else None
        batch = int(argv[3]) if len(argv) > 3 else 5
        cnt = summarize_once(cfg, folder=folder, batch=batch)
        print(f"summarize: {cnt or 0} items summarized")
        return 0

    if cmd == "summarize_job":
        summarize_job(cfg)
        print("summarize_job: done")
        return 0

    print(USAGE)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

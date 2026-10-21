"""python -m nsc <명령>

  collect [ltc] [welfare] [providers]   원천 수집 (기본: ltc welfare)
  build                                  화면 데이터·변경 기록 만들기
  sync pull | pull-site | push           R2 동기화
"""
import json
import sys

from . import build, sources, sync
from .core import DATA, ApiError, log, today, write_json


def collect(names):
    names = names or ["ltc", "welfare"]
    summary, errors = {"date": today()}, {}
    for n in names:
        try:
            summary.update(getattr(sources, n)())
        except ApiError as e:
            errors[n] = str(e)[:300]
            log(f"{n} 실패:", errors[n])
    summary["errors"] = errors
    write_json(DATA / "runs" / f"{today()}_{'-'.join(names)}.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if errors and len(errors) == len(names) else 0


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "collect":
        return collect(rest)
    if cmd == "build":
        build.main()
        return 0
    if cmd == "sync":
        {"pull": lambda: sync.pull(), "pull-site": lambda: sync.pull(site=True), "push": sync.push}[rest[0]]()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

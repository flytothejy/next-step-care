"""공통: 경로, 로그, 날짜, 공공데이터포털 호출·XML 해석, 파일 입출력."""
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "nsc"          # 로컬 작업 폴더. 커밋하지 않고 R2와 동기화
CONFIG = ROOT / "config"
KST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "next-step-care/0.1 (+https://github.com/flytothejy/next-step-care)"}


def today():
    return dt.datetime.now(KST).date().isoformat()


def log(*a):
    print(dt.datetime.now(KST).strftime("%H:%M:%S"), *a, flush=True)


def key():
    k = os.environ.get("DATA_GO_KR_KEY", "")
    if not k:
        sys.exit("DATA_GO_KR_KEY가 없습니다 (GitHub Secrets)")
    return k


def redact(s):
    k = os.environ.get("DATA_GO_KR_KEY", "")
    if k:
        s = s.replace(k, "***").replace(urllib.parse.quote(k), "***")
    return re.sub(r"(serviceKey=)[^&\"' ]+", r"\1***", s)


class ApiError(RuntimeError):
    pass


def get(url, params, timeout=40, tries=3):
    """공공데이터포털 GET. 인증·권한 오류는 바로 실패, 일시 오류는 재시도."""
    q = urllib.parse.urlencode({"serviceKey": key(), **params}, safe="")
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(f"{url}?{q}", headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
            if "SERVICE_KEY_IS_NOT_REGISTERED" in body or "NO_OPENAPI_SERVICE" in body:
                raise ApiError(redact(body[:300]))
            return body
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (401, 403):
                raise ApiError(f"{e.code} {redact(body[:300])}") from e
            last = f"{e.code} {redact(body[:200])}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = redact(str(e))
        time.sleep(2 * (i + 1))
    raise ApiError(f"{redact(url)} 실패: {last}")


def to_dict(el):
    """XML 요소 → dict. 같은 태그가 여러 번 나오면 리스트."""
    kids = list(el)
    if not kids:
        return (el.text or "").strip()
    out = {}
    for c in kids:
        v = to_dict(c)
        if c.tag in out:
            if not isinstance(out[c.tag], list):
                out[c.tag] = [out[c.tag]]
            out[c.tag].append(v)
        else:
            out[c.tag] = v
    return out


def parse(body, tag):
    """응답에서 tag 요소들을 dict 목록으로, totalCount와 함께."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ApiError(f"XML 해석 실패: {redact(body[:200])}") from e
    tc = root.findtext(".//totalCount")
    return [to_dict(x) for x in root.iter(tag)], (int(tc) if tc and tc.strip().isdigit() else None)


def page_all(url, params, tag="item", rows=100, max_pages=500):
    out, total = [], None
    for p in range(1, max_pages + 1):
        got, tc = parse(get(url, {**params, "pageNo": p, "numOfRows": rows}), tag)
        total = tc if tc is not None else total
        out += got
        if not got or (total is not None and len(out) >= total):
            break
    return out, total


def write_json(path, obj, gz=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if gz:
        with gzip.open(path, "wb") as f:
            f.write(data)
    else:
        path.write_bytes(data)


def read_json(path, default=None):
    if not path.exists():
        return default
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as f:
            return json.loads(f.read())
    return json.loads(path.read_text(encoding="utf-8"))


def regions():
    return json.loads((CONFIG / "regions.json").read_text(encoding="utf-8"))

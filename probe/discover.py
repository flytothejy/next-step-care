"""공공데이터포털 API 탐색: 상세 페이지에서 호출 주소·파라미터(swagger)를 찾고, 알려진 주소는 시험 호출한다.

결과는 probe/out/ 에 저장. 인증키는 어떤 출력에도 남기지 않는다.
"""
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

KEY = os.environ.get("DATA_GO_KR_KEY", "")
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)

DATASETS = {
    "15090532": "중앙부처복지서비스",
    "15108347": "지자체복지서비스",
    "15059029": "장기요양기관 검색",
    "15058856": "장기요양기관 시설별 상세",
    "15158798": "사회서비스 제공기관 정보 검색_GW",
    "15159019": "사회서비스 공통코드 조회_GW",
    "15021138": "전국치매센터표준데이터",
    "15155702": "전국발달재활서비스제공기관표준데이터",
}

UA = {"User-Agent": "Mozilla/5.0 (next-step-care probe)"}


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def redact(s):
    if KEY:
        s = s.replace(KEY, "***").replace(urllib.parse.quote(KEY), "***")
    return re.sub(r"(serviceKey=)[^&\"' ]+", r"\1***", s)


def discover(pk, name):
    info = {"pk": pk, "name": name}
    for kind in ("openapi", "standard"):
        url = f"https://www.data.go.kr/data/{pk}/{kind}.do"
        try:
            st, html = get(url)
        except Exception as e:  # noqa: BLE001
            info[f"{kind}_error"] = str(e)
            continue
        info[f"{kind}_status"] = st
        # swagger 관련 단서 수집
        hits = []
        for m in re.finditer(r"(?i)swagger", html):
            a, b = max(0, m.start() - 300), min(len(html), m.end() + 600)
            hits.append(html[a:b])
            if len(hits) >= 6:
                break
        info[f"{kind}_swagger_snippets"] = hits
        urls = sorted(set(re.findall(r"https?://apis\.data\.go\.kr/[A-Za-z0-9_/\-.]+", html)))
        info[f"{kind}_api_urls"] = urls
        m = re.search(r"var\s+swaggerJson\s*=\s*(`|'|\")(.+?)\1\s*;", html, re.S)
        if m:
            info[f"{kind}_swaggerJson_len"] = len(m.group(2))
            (OUT / f"swagger_{pk}.txt").write_text(m.group(2)[:200000], encoding="utf-8")
    return info


def call(label, url, params):
    q = urllib.parse.urlencode({"serviceKey": KEY, **params}, safe="")
    try:
        st, body = get(f"{url}?{q}")
    except Exception as e:  # noqa: BLE001
        return {"label": label, "url": url, "params": params, "error": redact(str(e))}
    return {"label": label, "url": url, "params": params, "status": st, "body": redact(body[:6000])}


def main():
    disc = [discover(pk, n) for pk, n in DATASETS.items()]
    (OUT / "discover.json").write_text(json.dumps(disc, ensure_ascii=False, indent=1), encoding="utf-8")

    calls = [
        call("중앙부처 목록(발달재활)",
             "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001",
             {"callTp": "L", "pageNo": 1, "numOfRows": 5, "srchKeyCode": "003", "searchWrd": "발달재활"}),
        call("중앙부처 목록(노인맞춤돌봄)",
             "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001",
             {"callTp": "L", "pageNo": 1, "numOfRows": 5, "srchKeyCode": "003", "searchWrd": "노인맞춤돌봄"}),
        call("지자체 목록(화성시)",
             "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfarelist",
             {"pageNo": 1, "numOfRows": 10, "ctpvNm": "경기도", "sggNm": "화성시"}),
    ]
    (OUT / "calls.json").write_text(json.dumps(calls, ensure_ascii=False, indent=1), encoding="utf-8")
    print("key set:", bool(KEY))
    for c in calls:
        print(c["label"], c.get("status"), c.get("error", "")[:120])


if __name__ == "__main__":
    main()

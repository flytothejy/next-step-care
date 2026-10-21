"""공공데이터포털 API 탐색 2차.

1) 키가 살아 있는지: 아이디어 뱅크가 쓰는 목록 API(GetSearchDataList)로 확인
2) 상세 페이지 HTML 전체 저장 (swagger를 불러오는 스크립트 확인용)
3) 대상 API 시험 호출 — 403이면 해당 API 활용신청이 안 된 것

결과는 probe/out/ 에 저장. 인증키는 어떤 출력에도 남기지 않는다.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KEY = os.environ.get("DATA_GO_KR_KEY", "")
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (next-step-care probe)"}


def redact(s):
    if KEY:
        s = s.replace(KEY, "***").replace(urllib.parse.quote(KEY), "***")
    return re.sub(r"(serviceKey=)[^&\"' ]+", r"\1***", s)


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def call(label, url, params):
    q = urllib.parse.urlencode({"serviceKey": KEY, **params}, safe="")
    st, body = fetch(f"{url}?{q}")
    return {"label": label, "url": url, "params": params, "status": st, "body": redact(body[:5000])}


def main():
    meta = {"key_set": bool(KEY), "key_len": len(KEY)}

    for pk in ("15059029", "15158798", "15090532"):
        st, html = fetch(f"https://www.data.go.kr/data/{pk}/openapi.do")
        (OUT / f"page_{pk}.html").write_text(redact(html), encoding="utf-8")

    calls = [
        call("키 확인: 공공데이터 목록 검색",
             "https://apis.data.go.kr/AAAAAAA/GetSearchDataList/v2/search",
             {"page": 1, "perPage": 1, "keyword": "장기요양"}),
        call("장기요양기관 상세(주소 발견분)",
             "https://apis.data.go.kr/B550928/getLtcInsttDetailInfoService02/getConvInsttDetailInfoList02",
             {"pageNo": 1, "numOfRows": 3}),
        call("중앙부처 목록(발달재활)",
             "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001",
             {"callTp": "L", "pageNo": 1, "numOfRows": 5, "srchKeyCode": "003", "searchWrd": "발달재활"}),
        call("지자체 목록(화성시)",
             "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfarelist",
             {"pageNo": 1, "numOfRows": 10, "ctpvNm": "경기도", "sggNm": "화성시"}),
    ]
    (OUT / "calls2.json").write_text(json.dumps({"meta": meta, "calls": calls}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(meta)
    for c in calls:
        print(c["label"], c["status"], c["body"][:200].replace("\n", " "))


if __name__ == "__main__":
    main()

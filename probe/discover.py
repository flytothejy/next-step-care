"""공공데이터포털 API 탐색 3차.

1) 각 데이터 상세 페이지에서 swagger 명세를 뽑아 저장
2) 명세에 적힌 예시 값으로 오퍼레이션마다 시험 호출
3) 화성시 대상 실제 호출 (장기요양기관, 지자체 복지서비스, 중앙부처 복지서비스)

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

PAGES = {
    "15090532": ("openapi", "중앙부처복지서비스"),
    "15108347": ("openapi", "지자체복지서비스"),
    "15059029": ("openapi", "장기요양기관 검색"),
    "15058856": ("openapi", "장기요양기관 시설별 상세"),
    "15158798": ("openapi", "사회서비스 제공기관 정보 검색_GW"),
    "15159019": ("openapi", "사회서비스 공통코드 조회_GW"),
    "15155702": ("standard", "발달재활서비스제공기관 표준"),
    "15021138": ("standard", "치매센터 표준"),
}


def redact(s):
    if KEY:
        s = s.replace(KEY, "***").replace(urllib.parse.quote(KEY), "***")
    return re.sub(r"(serviceKey=)[^&\"' ]+", r"\1***", s)


def fetch(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def call(base, params, limit=4000):
    q = urllib.parse.urlencode({"serviceKey": KEY, **params}, safe="")
    st, body = fetch(f"{base}?{q}")
    return {"url": base, "params": params, "status": st, "body": redact(body[:limit])}


def swagger_of(pk, kind):
    st, html = fetch(f"https://www.data.go.kr/data/{pk}/{kind}.do")
    m = re.search(r"swaggerJson\s*=\s*`(.*?)`\s*;", html, re.S)
    if not m:
        return None
    raw = m.group(1).replace("\\\\", "\\")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            (OUT / f"swagger_raw_{pk}.txt").write_text(m.group(1)[:100000], encoding="utf-8")
            return None


def main():
    report = {"key_set": bool(KEY), "apis": []}
    for pk, (kind, name) in PAGES.items():
        sw = swagger_of(pk, kind)
        entry = {"pk": pk, "name": name, "swagger": bool(sw)}
        if not sw:
            report["apis"].append(entry)
            continue
        (OUT / f"swagger_{pk}.json").write_text(json.dumps(sw, ensure_ascii=False, indent=1), encoding="utf-8")
        host = sw.get("host", "")
        entry["host"] = host
        ops = []
        for vo in sw.get("swaggerOprtinVOs", []) or []:
            op = vo.get("operationId") or vo.get("gwSvcNm")
            params = {p["paramtrNm"]: p.get("paramtrBassValue", "") for p in vo.get("reqList", [])
                      if p.get("paramtrNm") and p.get("paramtrNm") != "serviceKey" and p.get("paramtrBassValue")}
            res = call(f"https://{host}/{op}", params, limit=2500)
            ops.append({"op": op, "name": vo.get("oprtinNm"), "req": [(p.get("paramtrNm"), p.get("paramtrDivision"), p.get("paramtrDc")) for p in vo.get("reqList", [])],
                        "res_fields": [(p.get("paramtrNm"), p.get("paramtrDc")) for p in vo.get("resList", [])][:60],
                        "sample_status": res["status"], "sample_body": res["body"]})
        if not ops:  # 표준데이터 등 VO가 없으면 paths만 기록
            ops = [{"path": p} for p in (sw.get("paths") or {}).keys()]
        entry["ops"] = ops
        report["apis"].append(entry)

    # 화성시 실제 호출
    hw = {}
    ltc = "https://apis.data.go.kr/B550928/searchLtcInsttService02/getLtcInsttSeachList02"
    hw["장기요양_경기_전체_1쪽"] = call(ltc, {"siDoCd": "41", "pageNo": 1, "numOfRows": 100}, limit=20000)
    for code in ("590", "591", "593", "595", "597"):
        hw[f"장기요양_경기_{code}"] = call(ltc, {"siDoCd": "41", "siGunGuCd": code, "pageNo": 1, "numOfRows": 5}, limit=3000)
    hw["지자체복지_화성시"] = call("https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfarelist",
                               {"pageNo": 1, "numOfRows": 50, "ctpvNm": "경기도", "sggNm": "화성시"}, limit=30000)
    for w in ("발달재활", "노인맞춤돌봄", "장기요양", "활동지원"):
        hw[f"중앙부처_{w}"] = call("https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001",
                                {"callTp": "L", "pageNo": 1, "numOfRows": 5, "srchKeyCode": "003", "searchWrd": w}, limit=6000)
    report["hwaseong"] = hw

    (OUT / "report3.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for a in report["apis"]:
        print(a["pk"], a["name"], a.get("host"), [(o.get("op"), o.get("sample_status")) for o in a.get("ops", [])])
    for k, v in hw.items():
        print(k, v["status"], v["body"][:120].replace("\n", " "))


if __name__ == "__main__":
    main()

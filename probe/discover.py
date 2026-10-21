"""공공데이터포털 API 탐색 4차: 화성시 데이터를 끝까지 받아 채움 정도를 본다.

결과는 probe/out/ 에 저장. 인증키는 어떤 출력에도 남기지 않는다.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

KEY = os.environ.get("DATA_GO_KR_KEY", "")
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (next-step-care probe)"}


def redact(s):
    if KEY:
        s = s.replace(KEY, "***").replace(urllib.parse.quote(KEY), "***")
    return re.sub(r"(serviceKey=)[^&\"' ]+", r"\1***", s)


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def api(base, params):
    q = urllib.parse.urlencode({"serviceKey": KEY, **params}, safe="")
    return fetch(f"{base}?{q}")


def items(xml_text, tag="item"):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, None
    tc = root.findtext(".//totalCount")
    rows = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter(tag)]
    return rows, int(tc) if tc and tc.isdigit() else None


def page_all(base, params, tag="item", rows=1000, max_pages=60):
    allrows, total, errors = [], None, []
    for p in range(1, max_pages + 1):
        st, body = api(base, {**params, "pageNo": p, "numOfRows": rows})
        got, tc = items(body, tag)
        if got is None:
            errors.append({"page": p, "status": st, "body": redact(body[:300])})
            break
        total = tc if tc is not None else total
        allrows += got
        if not got or (total is not None and len(allrows) >= total):
            break
    return allrows, total, errors


def main():
    rep = {}

    # 1) 장기요양기관: 화성시 시군구코드 후보 전부
    ltc = "https://apis.data.go.kr/B550928/searchLtcInsttService02/getLtcInsttSeachList02"
    ltc_rows = []
    for code in ("590", "591", "592", "593", "594", "595", "596", "597"):
        rows, total, err = page_all(ltc, {"siDoCd": "41", "siGunGuCd": code}, rows=500)
        rep.setdefault("ltc_by_code", {})[code] = {"total": total, "got": len(rows), "errors": err,
                                                  "types": Counter(r.get("adminPttnCd") for r in rows).most_common()}
        ltc_rows += rows
    (OUT / "ltc_hwaseong.json").write_text(json.dumps(ltc_rows, ensure_ascii=False, indent=0), encoding="utf-8")

    # 2) 장기요양기관 상세: 페이지에서 명세를 찾아 저장 + 알려진 주소로 시험
    st, html = fetch("https://www.data.go.kr/data/15058856/openapi.do")
    (OUT / "page_15058856.html").write_text(redact(html), encoding="utf-8")
    if ltc_rows:
        sym = ltc_rows[0].get("longTermAdminSym")
        for op in ("getGeneralSttusDetailInfoItem02", "getConvInsttDetailInfoList02", "getLtcInsttDetailInfoItem02"):
            st, body = api(f"https://apis.data.go.kr/B550928/getLtcInsttDetailInfoService02/{op}",
                           {"longTermAdminSym": sym, "adminPttnCd": ltc_rows[0].get("adminPttnCd", "")})
            rep.setdefault("ltc_detail_try", {})[op] = {"status": st, "body": redact(body[:1500])}

    # 3) 사회서비스 제공기관: 사업구분 → 화성시만
    st, body = api("https://apis.data.go.kr/B554287/common/serviceType", {})
    types, _ = items(body)
    rep["service_types"] = types if types is not None else redact(body[:500])
    want = [t.get("serviceTypeName") for t in (types or [])
            if re.search(r"발달|활동지원|장애|언어|재활|노인|돌봄|가사|간병", t.get("serviceTypeName", ""))]
    prov = {}
    for name in want:
        rows, total, err = page_all("https://apis.data.go.kr/B554287/provider/providerList", {"serviceTypeName": name}, rows=1000)
        hw = [r for r in rows if "화성" in (r.get("signguName", "") + r.get("address", "") + r.get("loadAddress", ""))]
        prov[name] = {"total": total, "got": len(rows), "hwaseong": len(hw), "errors": err,
                      "services": Counter(r.get("serviceName") for r in hw).most_common(15),
                      "with_tel": sum(1 for r in hw if r.get("telNumber")), "sample": hw[:3]}
        (OUT / f"provider_{re.sub(r'[^0-9A-Za-z가-힣]', '_', name)}_hwaseong.json").write_text(
            json.dumps(hw, ensure_ascii=False, indent=0), encoding="utf-8")
    rep["providers"] = prov

    # 4) 복지서비스: 화성시 지자체 전체 + 관련 중앙부처 상세
    rows, total, _ = page_all("https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfarelist",
                              {"ctpvNm": "경기도", "sggNm": "화성시"}, tag="servList", rows=100)
    rep["local_welfare_hwaseong"] = [{k: r.get(k) for k in ("servId", "servNm", "lifeNmArray", "trgterIndvdlNmArray", "intrsThemaNmArray", "bizChrDeptNm", "lastModYmd")} for r in rows]
    nat = {}
    for w in ("발달재활", "노인맞춤돌봄", "장기요양", "활동지원", "발달", "치매", "통합돌봄", "퇴원"):
        st, body = api("https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001",
                       {"callTp": "L", "pageNo": 1, "numOfRows": 20, "srchKeyCode": "003", "searchWrd": w})
        r, tc = items(body, "servList")
        nat[w] = {"total": tc, "list": [{k: x.get(k) for k in ("servId", "servNm", "jurOrgNm", "lifeArray", "lastModYmd")} for x in (r or [])]}
    rep["national_search"] = nat
    sid = next((x["servId"] for x in nat["발달재활"]["list"] if "발달재활" in (x.get("servNm") or "")), None)
    if sid:
        st, body = api("https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfaredetailedV001",
                       {"callTp": "D", "servId": sid})
        rep["national_detail_sample"] = redact(body[:8000])

    # 5) 표준데이터 페이지 저장 (호출 주소 확인용)
    for pk in ("15155702", "15021138"):
        st, html = fetch(f"https://www.data.go.kr/data/{pk}/standard.do")
        (OUT / f"page_{pk}.html").write_text(redact(html), encoding="utf-8")

    (OUT / "report4.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: (v if k != "providers" else {n: {kk: vv for kk, vv in d.items() if kk != "sample"} for n, d in v.items()}) for k, v in rep.items() if k in ("ltc_by_code", "providers")}, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    main()

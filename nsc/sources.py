"""원천 수집. 각 수집기는 snap/<날짜>/<이름>.json.gz 에 응답을 거의 그대로 저장한다.

- ltc            장기요양기관 목록(지역별) + 상세(캐시, 7일마다 갱신)       매일
- welfare        중앙부처·지자체 복지서비스 목록 + 상세(변경된 것만)        매일
- providers      사회서비스 제공기관 전국 목록 (지역 필터가 없어 전량)      매주
"""
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

from .core import DATA, ApiError, budget_left, get, log, page_all, parse, read_json, regions, today, write_json

LTC_LIST = "https://apis.data.go.kr/B550928/searchLtcInsttService02/getLtcInsttSeachList02"
LTC_DETAIL = "https://apis.data.go.kr/B550928/getLtcInsttDetailInfoService02/"
# 장기요양기관 상세 조회 9종: (이름, 오퍼레이션, 목록형 여부)
LTC_OPS = [
    ("general", "getGeneralSttusDetailInfoItem02", False),     # 일반현황: 주소코드·전화·층
    ("capacity", "getAceptncNmprDetailInfoItem02", False),     # 입소인원: 정원·현원·대기
    ("nonpay", "getNonBenefitSttusDetailInfoList02", True),    # 비급여: 식사비 등 추가 비용
    ("programs", "getProgramSttusDetailInfoList02", True),     # 프로그램
    ("staff", "getStaffSttusDetailInfoItem02", False),         # 인력
    ("facility", "getInsttSttusDetailInfoItem02", False),      # 시설: 1~4인실, 프로그램실 등
    ("etc", "getInsttEtcDetailInfoItem02", False),             # 홈페이지·교통편·주차
    ("partners", "getConvInsttDetailInfoList02", True),        # 협약 의료기관
    ("tools", "getWlfareToolDetailInfoList02", True),          # 복지용구
]
NAT_LIST = "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001"
NAT_DETAIL = "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfaredetailedV001"
LOC_LIST = "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfarelist"
LOC_DETAIL = "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfaredetailed"
PROVIDER_LIST = "https://apis.data.go.kr/B554287/provider/providerList"
# 표준데이터 API (전국, JSON). 이름 → 주소
STANDARD = {
    "dev_rehab": "https://api.data.go.kr/openapi/tn_pubr_public_developmental_rehabilitation_service_provider_api",
    "dementia_center": "https://api.data.go.kr/openapi/tn_pubr_public_imbclty_cnter_api",
}

DETAIL_MAX_AGE_DAYS = 7
DETAIL_CAP_PER_RUN = 400      # 하루 호출량 보호 (복지서비스)
LTC_CAP_PER_RUN = 600         # 기관 수 기준 (기관당 9회 호출)


def snap(name):
    return DATA / "snap" / today() / f"{name}.json.gz"


def _stale(fetched, days=DETAIL_MAX_AGE_DAYS):
    return not fetched or (dt.date.fromisoformat(today()) - dt.date.fromisoformat(fetched)).days >= days


def fetch_details(todo, call, workers=6, reserve=180):
    """상세를 병렬로 받는다. 남은 시간이 reserve초 아래로 떨어지면 새 요청을 멈추고 받은 것만 돌려준다."""
    done = {}

    def one(x):
        if budget_left() < reserve:
            return x, None, "time"
        try:
            return x, call(x), None
        except ApiError as e:
            return x, None, str(e)[:120]

    skipped, errors = 0, []
    with ThreadPoolExecutor(workers) as ex:
        for x, res, err in ex.map(one, todo):
            if err == "time":
                skipped += 1
            elif err:
                errors.append(err)
            else:
                done[id(x)] = (x, res)
    if skipped or errors:
        log(f"상세: 시간 부족으로 미룸 {skipped}, 실패 {len(errors)}", errors[:3])
    LAST_ERRORS[:] = errors[:5]
    return list(done.values())


LAST_ERRORS = []


def ltc():
    out = {}
    for rk, r in regions().items():
        rows = []
        for code in r["ltc"]["siGunGuCd"]:
            got, total = page_all(LTC_LIST, {"siDoCd": r["ltc"]["siDoCd"], "siGunGuCd": code}, rows=100)
            log(f"장기요양 {rk} {code}: {len(got)}/{total}")
            rows += got
        out[rk] = rows

    cache_p = DATA / "state" / "ltc_detail.json"
    cache = read_json(cache_p, {})
    todo = [x for rows in out.values() for x in rows
            if "capacity" not in (cache.get(x.get("longTermAdminSym")) or {})
            or _stale((cache.get(x.get("longTermAdminSym")) or {}).get("fetched"))][:LTC_CAP_PER_RUN]

    def one(x):
        sym, kind = x.get("longTermAdminSym"), x.get("adminPttnCd", "")
        rec = {"fetched": today()}
        for name, op, many in LTC_OPS:
            items, _ = parse(get(LTC_DETAIL + op, {"longTermAdminSym": sym, "adminPttnCd": kind,
                                                   "pageNo": 1, "numOfRows": 100}), "item")
            rec[name] = items if many else (items[0] if items else None)
        return rec

    got = fetch_details(todo, one, workers=4, reserve=240)
    for x, rec in got:
        cache[x.get("longTermAdminSym")] = rec
    ok, errs = len(got), list(LAST_ERRORS)
    log(f"장기요양 상세 {ok}/{len(todo)}기관 갱신 (캐시 {len(cache)})")
    write_json(cache_p, cache)
    write_json(snap("ltc"), {"list": out, "detail": cache}, gz=True)
    return {"ltc": {k: len(v) for k, v in out.items()}, "ltc_detail_refreshed": ok,
            "ltc_detail_pending": len(todo) - ok, "ltc_detail_errors": errs}


# 중앙부처 복지서비스 상세는 개발계정 하루 100회 한도 → 하루 80건, 노인·장애·돌봄 관련부터
NATIONAL_DAILY_CAP = 80
PRIORITY = ("노년", "장애", "돌봄", "보호", "요양", "발달", "치매", "활동지원", "재활")


def _priority(x):
    text = " ".join(str(x.get(k) or "") for k in ("servNm", "lifeArray", "trgterIndvdlArray", "intrsThemaArray"))
    return 0 if any(w in text for w in PRIORITY) else 1


def _welfare_details(kind, rows, url, extra, cap=DETAIL_CAP_PER_RUN):
    cache_p = DATA / "state" / f"welfare_{kind}_detail.json"
    cache = read_json(cache_p, {})
    todo = [x for x in rows
            if x.get("servId") and ((cache.get(x["servId"]) or {}).get("lastModYmd") != x.get("lastModYmd")
                                    or _stale((cache.get(x["servId"]) or {}).get("fetched"), 30))]
    todo = sorted(todo, key=lambda x: ((cache.get(x["servId"]) is not None), _priority(x)))[:cap]
    # 초당 호출 제한에 걸리지 않도록 동시 요청을 낮춘다
    got = fetch_details(todo, lambda x: parse(get(url, {**extra, "servId": x["servId"]}), "wantedDtl")[0], workers=2)
    for x, items in got:
        cache[x["servId"]] = {"fetched": today(), "lastModYmd": x.get("lastModYmd"), "item": items[0] if items else None}
    ok = len(got)
    write_json(cache_p, cache)
    log(f"복지서비스 {kind} 상세 {ok}/{len(todo)}건 갱신 (캐시 {len(cache)})")
    ERRORS[kind] = {"todo": len(todo), "ok": ok, "errors": list(LAST_ERRORS)}
    return cache, ok


ERRORS = {}


def _national_list():
    """중앙부처 목록. 검색어 없는 전체 조회가 안 되면 주제 키워드로 나눠 받아 합친다."""
    body = get(NAT_LIST, {"callTp": "L", "pageNo": 1, "numOfRows": 50})
    first, total = parse(body, "servList")
    if first:
        rows, total = page_all(NAT_LIST, {"callTp": "L"}, tag="servList", rows=50)
        return rows, total, "전체"
    log("중앙부처 전체 조회 결과 없음:", body[:300].replace("\n", " "))
    seen = {}
    for w in ("장애", "노인", "돌봄", "요양", "발달", "치매", "활동지원", "재활", "보호자", "간병", "퇴원", "아동", "가족"):
        rows, _ = page_all(NAT_LIST, {"callTp": "L", "srchKeyCode": "003", "searchWrd": w}, tag="servList", rows=50)
        for r in rows:
            seen.setdefault(r.get("servId"), r)
    return list(seen.values()), len(seen), "키워드"


def welfare():
    nat, total, how = _national_list()
    log(f"중앙부처 복지서비스 {len(nat)}/{total} ({how})")
    nat_cache, nat_ok = _welfare_details("national", nat, NAT_DETAIL, {"callTp": "D"}, cap=NATIONAL_DAILY_CAP)

    loc = {}
    for rk, r in regions().items():
        rows, total = page_all(LOC_LIST, r["local_welfare"], tag="servList", rows=50)
        log(f"지자체 복지서비스 {rk} {len(rows)}/{total}")
        loc[rk] = rows
    loc_cache, loc_ok = _welfare_details("local", [x for v in loc.values() for x in v], LOC_DETAIL, {})

    write_json(snap("welfare"), {
        "national": {"list": nat, "detail": {k: v["item"] for k, v in nat_cache.items()}},
        "local": {"list": loc, "detail": {k: v["item"] for k, v in loc_cache.items()}},
    }, gz=True)
    return {"welfare_national": len(nat), "welfare_local": {k: len(v) for k, v in loc.items()},
            "welfare_detail_refreshed": nat_ok + loc_ok, "welfare_detail_status": dict(ERRORS)}


def providers(rows=1000, workers=4):
    first, total = parse(get(PROVIDER_LIST, {"pageNo": 1, "numOfRows": rows}), "item")
    pages = -(-(total or 0) // rows)
    log(f"사회서비스 제공기관 전국 {total}건, {pages}쪽")

    def one(p):
        for i in range(3):
            try:
                return parse(get(PROVIDER_LIST, {"pageNo": p, "numOfRows": rows}, timeout=90), "item")[0]
            except ApiError as e:
                log("제공기관", p, "쪽 재시도", i + 1, str(e)[:100])
        return None

    allrows, missing = list(first), []
    with ThreadPoolExecutor(workers) as ex:
        for p, got in zip(range(2, pages + 1), ex.map(one, range(2, pages + 1))):
            if got is None:
                missing.append(p)
            else:
                allrows += got
    log(f"제공기관 받음 {len(allrows)}/{total}, 실패 쪽 {missing[:10]}")
    write_json(snap("providers"), {"total": total, "rows": allrows, "missing_pages": missing}, gz=True)
    return {"providers": len(allrows), "providers_total": total, "providers_missing_pages": len(missing)}


def standard(rows=1000):
    """표준데이터 API: 전국 전량을 받아 그대로 저장 (화성시 추림은 build에서)."""
    out, status = {}, {}
    for name, url in STANDARD.items():
        allrows, total, page = [], None, 1
        try:
            while True:
                raw = get(url, {"pageNo": page, "numOfRows": rows, "type": "json"})
                if page == 1:
                    status[name] = {"sample": raw[:400]}
                body = json.loads(raw)
                b = (body.get("response") or {}).get("body") or {}
                items = b.get("items") or []
                if isinstance(items, dict):
                    items = items.get("item") or []
                total = int(b.get("totalCount") or 0)
                allrows += items
                if not items or len(allrows) >= total or page > 50:
                    break
                page += 1
            status[name] = {**status.get(name, {}), "rows": len(allrows), "total": total}
        except (ApiError, ValueError) as e:
            status[name] = {**status.get(name, {}), "error": str(e)[:200], "rows": len(allrows)}
        out[name] = allrows
        log(f"표준데이터 {name}: {status[name]}")
    write_json(snap("standard"), out, gz=True)
    return {"standard": status}


# 파일 데이터 (포털 내려받기). 이름 → 공공데이터 번호
FILES = {
    "ltc_eval": "15104801",        # 국민건강보험공단 장기요양기관 평가 결과
    "dementia_ltc": "15145962",    # 국민건강보험공단 치매관련급여 제공 장기요양기관 현황
}


def _portal(url, data=None, timeout=60):
    import urllib.parse
    import urllib.request
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": "Mozilla/5.0 (next-step-care)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), dict(r.headers)


def _decode_table(raw):
    import csv
    import io
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None
    return list(csv.DictReader(io.StringIO(text)))


def files():
    """포털 파일 데이터: 상세 페이지 → selectFileDataDownload.do(파일 번호) → 파일 내려받기 → CSV 해석."""
    import re
    out, status = {}, {}
    for name, pk in FILES.items():
        st = status.setdefault(name, {})
        try:
            page = _portal(f"https://www.data.go.kr/data/{pk}/fileData.do")[0].decode("utf-8", "replace")
            m = re.search(r"fn_fileDataDown\('(\d+)',\s*'([^']+)',\s*'([^']*)',\s*'(\d+)'", page)
            if not m:
                st["error"] = "다운로드 버튼을 찾지 못함"
                continue
            meta, _ = _portal("https://www.data.go.kr/tcs/dss/selectFileDataDownload.do",
                              {"publicDataPk": m.group(1), "publicDataDetailPk": m.group(2), "atchFileId": m.group(3),
                               "fileDetailSn": m.group(4), "publicDataTyCode": "PR0051"})
            info = json.loads(meta.decode("utf-8", "replace"))
            st["meta"] = {k: info.get(k) for k in ("status", "atchFileId", "fileDetailSn")}
            fid, sn = info.get("atchFileId"), info.get("fileDetailSn")
            raw = None
            for cand in (f"https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId={fid}&fileDetailSn={sn}",
                         f"https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId={fid}&fileDetailSn={sn}&dataNm={name}"):
                try:
                    raw, headers = _portal(cand)
                    st["url_used"] = cand.split("?")[0]
                    st["content_type"] = headers.get("Content-Type")
                    break
                except Exception as e:  # noqa: BLE001
                    st.setdefault("tries", []).append(str(e)[:120])
            if raw is None:
                continue
            (DATA / "snap" / today()).mkdir(parents=True, exist_ok=True)
            (DATA / "snap" / today() / f"file_{name}.bin").write_bytes(raw)
            rows = _decode_table(raw)
            st["bytes"] = len(raw)
            st["rows"] = len(rows) if rows is not None else None
            st["columns"] = list(rows[0].keys())[:40] if rows else None
            out[name] = rows or []
        except Exception as e:  # noqa: BLE001
            st["error"] = str(e)[:200]
        log(f"파일 {name}: {st}")
    write_json(snap("files"), out, gz=True)
    return {"files": status}

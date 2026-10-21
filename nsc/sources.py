"""원천 수집. 각 수집기는 snap/<날짜>/<이름>.json.gz 에 응답을 거의 그대로 저장한다.

- ltc            장기요양기관 목록(지역별) + 상세(캐시, 7일마다 갱신)       매일
- welfare        중앙부처·지자체 복지서비스 목록 + 상세(변경된 것만)        매일
- providers      사회서비스 제공기관 전국 목록 (지역 필터가 없어 전량)      매주
"""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from .core import DATA, ApiError, budget_left, get, log, page_all, parse, read_json, regions, today, write_json

LTC_LIST = "https://apis.data.go.kr/B550928/searchLtcInsttService02/getLtcInsttSeachList02"
LTC_DETAIL = "https://apis.data.go.kr/B550928/getLtcInsttDetailInfoService02/getGeneralSttusDetailInfoItem02"
NAT_LIST = "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001"
NAT_DETAIL = "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfaredetailedV001"
LOC_LIST = "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfarelist"
LOC_DETAIL = "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfaredetailed"
PROVIDER_LIST = "https://apis.data.go.kr/B554287/provider/providerList"

DETAIL_MAX_AGE_DAYS = 7
DETAIL_CAP_PER_RUN = 400      # 하루 호출량 보호


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

    skipped = failed = 0
    with ThreadPoolExecutor(workers) as ex:
        for x, res, err in ex.map(one, todo):
            if err == "time":
                skipped += 1
            elif err:
                failed += 1
            else:
                done[id(x)] = (x, res)
    if skipped or failed:
        log(f"상세: 시간 부족으로 미룸 {skipped}, 실패 {failed}")
    return list(done.values())


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
            if _stale((cache.get(x.get("longTermAdminSym")) or {}).get("fetched"))]
    todo = todo[:DETAIL_CAP_PER_RUN]
    got = fetch_details(todo, lambda x: parse(get(LTC_DETAIL, {"longTermAdminSym": x.get("longTermAdminSym"),
                                                               "adminPttnCd": x.get("adminPttnCd", "")}), "item")[0], workers=4)
    for x, items in got:
        cache[x.get("longTermAdminSym")] = {"fetched": today(), "item": items[0] if items else None}
    ok = len(got)
    log(f"장기요양 상세 {ok}/{len(todo)}건 갱신 (캐시 {len(cache)})")
    write_json(cache_p, cache)
    write_json(snap("ltc"), {"list": out, "detail": {s: v["item"] for s, v in cache.items()}}, gz=True)
    return {"ltc": {k: len(v) for k, v in out.items()}, "ltc_detail_refreshed": ok}


def _welfare_details(kind, rows, url, extra):
    cache_p = DATA / "state" / f"welfare_{kind}_detail.json"
    cache = read_json(cache_p, {})
    todo = [x for x in rows
            if x.get("servId") and ((cache.get(x["servId"]) or {}).get("lastModYmd") != x.get("lastModYmd")
                                    or _stale((cache.get(x["servId"]) or {}).get("fetched"), 30))][:DETAIL_CAP_PER_RUN]
    got = fetch_details(todo, lambda x: parse(get(url, {**extra, "servId": x["servId"]}), "wantedDtl")[0])
    for x, items in got:
        cache[x["servId"]] = {"fetched": today(), "lastModYmd": x.get("lastModYmd"), "item": items[0] if items else None}
    ok = len(got)
    write_json(cache_p, cache)
    log(f"복지서비스 {kind} 상세 {ok}/{len(todo)}건 갱신 (캐시 {len(cache)})")
    return cache, ok


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
    nat_cache, nat_ok = _welfare_details("national", nat, NAT_DETAIL, {"callTp": "D"})

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
            "welfare_detail_refreshed": nat_ok + loc_ok}


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

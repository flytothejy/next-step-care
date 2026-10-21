"""가장 최근 수집본으로 지역별 화면 데이터를 만들고, 지난번과 달라진 점을 기록한다.

출력
  site/data/<지역>/institutions.json   기관 (장기요양 + 사회서비스 제공기관 + 치매안심센터)
  site/data/<지역>/programs.json       제도 (중앙부처 + 해당 지자체 복지서비스), 제공기관 수 연결
  site/data/<지역>/meta.json           건수, 항목별 채움, 원천별 수집일
  events/<날짜>.json                   추가·삭제·변경 (알림·주간 검토용)
"""
import hashlib
import json
import re
from collections import Counter, defaultdict

from .core import DATA, log, read_json, regions, today, write_json

# 장기요양기관 유형 코드. 공통코드 문서로 확인되지 않은 이름은 verified=False 로 둔다.
LTC_KIND = {
    "A03": ("노인요양시설", False),
    "A04": ("노인요양공동생활가정", False),
    "B01": ("방문요양", False),
    "B02": ("방문목욕", False),
    "B03": ("주야간보호", False),
    "B04": ("단기보호", False),
    "B05": ("방문간호", False),
    "B06": ("복지용구", False),
}

PLACEHOLDER_TEL = re.compile(r"(^|-)0{3,4}-0{4}$|^0+$")

# 사회서비스 제공기관 중 돌봄·장애와 관련된 사업구분만 남긴다 (생리용품·에너지바우처 등 제외)
CARE_TYPES = {
    "노인돌봄종합서비스", "가사간병방문지원", "장애아동가족지원", "장애인활동지원", "장애인활동보조",
    "지역사회서비스투자", "긴급돌봄지원", "장애인개인예산제", "일상돌봄서비스", "발달장애인지원",
}

GU = re.compile(r"(동탄구|만세구|효행구|병점구)")
TOKEN = re.compile(r"([가-힣0-9]+(?:읍|면|동|로|길))(?=[\s,(]|$)")


def latest(name):
    snaps = sorted((DATA / "snap").glob(f"*/{name}.json.gz"))
    if not snaps:
        return None, None
    return read_json(snaps[-1]), snaps[-1].parent.name


def tel(s):
    s = (s or "").strip()
    return None if not s or PLACEHOLDER_TEL.search(s) else s


def num(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def h(obj):
    return hashlib.sha1(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def norm_name(s):
    return re.sub(r"[\s()\[\]·.,\-]|주식회사|\(주\)|사회적협동조합", "", s or "")


def tokens(addr):
    return [t for t in TOKEN.findall(addr or "") if t not in ("화성시",)]


# ── 구 판별: 데이터에서 배운다 ────────────────────────────────────────────

def learn_gu(rows, prefix):
    """구가 적힌 기관의 읍면동·도로명 → 구 사전. 한 토큰이 두 구에 걸치면 다수결, 애매하면 버린다."""
    votes = defaultdict(Counter)
    for r in rows:
        if not (r.get("signguName") or "").startswith(prefix):
            continue
        addr = r.get("loadAddress") or r.get("address") or ""
        m = GU.search((r.get("signguName") or "") + " " + addr)
        if not m:
            continue
        for t in tokens(addr):
            votes[t][m.group(1)] += 1
    table = {}
    for t, c in votes.items():
        (g, n), total = c.most_common(1)[0], sum(c.values())
        if n >= 2 and n / total >= 0.9:
            table[t] = g
    return table


def guess_gu(addr, table):
    m = GU.search(addr or "")
    if m:
        return m.group(1), "주소"
    c = Counter(table[t] for t in tokens(addr) if t in table)
    if c:
        g, n = c.most_common(1)[0]
        if n == sum(c.values()):
            return g, "읍면동·도로명"
    return None, None


# ── 기관 ─────────────────────────────────────────────────────────────────

def ltc_record(d):
    """상세 9종 → 화면용 요약. 옛 캐시 형식(일반현황만)도 받는다."""
    if "general" not in d and d:
        d = {"general": d}
    g = d.get("general") or {}
    cap = d.get("capacity") or {}
    staff = d.get("staff") or {}
    fac = d.get("facility") or {}
    etc = d.get("etc") or {}
    now = sum(x for x in (num(cap.get("maNowPer")), num(cap.get("fmNowPer"))) if x is not None) if cap else None
    wait = sum(x for x in (num(cap.get("maRsvPer")), num(cap.get("fmRsvPer"))) if x is not None) if cap else None
    rooms = {k: num(fac.get(f)) for k, f in (("1인실", "prsnRoomreal1"), ("2인실", "prsnRoomreal2"),
                                             ("3인실", "prsnRoomreal3"), ("4인실", "prsnRoomreal4")) if num(fac.get(f))}
    staff_sum = {k: v for k, v in (
        ("간호", (num(staff.get("nur")) or 0) + (num(staff.get("nurArticle")) or 0)),
        ("물리·작업치료", (num(staff.get("physicalMTret")) or 0) + (num(staff.get("wrkMTret")) or 0)),
        ("사회복지사", num(staff.get("socWel")) or 0),
        ("요양보호사", (num(staff.get("recuProt_1")) or 0) + (num(staff.get("recuProt_2")) or 0)),
        ("영양사", num(staff.get("nut")) or 0),
        ("의사", (num(staff.get("chrgDoc")) or 0) + (num(staff.get("chargeDoc")) or 0)),
    ) if v} if staff else None
    return {
        "tel": tel("-".join(p for p in (g.get("locTelNo_1"), g.get("locTelNo_2"), g.get("locTelNo_3")) if p)),
        "floor": num(g.get("fl")),
        "addr_codes": {k: g.get(k) for k in ("roadNmCd", "gunmulMlno", "gunmulSlno", "hmPostNo", "BDongCd", "HDongCd") if g.get(k)},
        "capacity": {"정원": num(cap.get("totPer")), "현원": now, "대기": wait} if cap else None,
        "fees": [{"금액": num(x.get("nonpayTgtAmt")), "산출근거": x.get("prodBase"), "종류": x.get("nonpayKind"),
                  "등록일": x.get("uptDt")} for x in (d.get("nonpay") or [])] or None,
        "programs": sorted({x.get("pgmNm") for x in (d.get("programs") or []) if x.get("pgmNm")}) or None,
        "staff": staff_sum,
        "rooms": rooms or None,
        "homepage": etc.get("hmpgAddr") or None,
        "transport": etc.get("tfMth") or None,
        "parking": etc.get("pkngEquip") or None,
        "partners": [x.get("yoyangNm") for x in (d.get("partners") or []) if x.get("yoyangNm")] or None,
        "detail_fetched": d.get("fetched"),
    }


def ltc_institutions(snap, rk):
    out = []
    detail = snap.get("detail", {})
    for x in snap.get("list", {}).get(rk, []):
        sym = x.get("longTermAdminSym")
        d = detail.get(sym) or {}
        if "item" in d and "general" not in d:          # 옛 캐시 형식
            d = {"general": d.get("item") or {}, "fetched": d.get("fetched")}
        kind, verified = LTC_KIND.get(x.get("adminPttnCd"), (None, False))
        out.append({
            "id": f"ltc:{sym}", "source": "장기요양기관", "name": x.get("adminNm"),
            "kind_code": x.get("adminPttnCd"), "kind": kind, "kind_verified": verified,
            "services": [kind] if kind else [],
            "area_code": f'{x.get("siDoCd")}{x.get("siGunGuCd")}', "gu": None, "gu_from": None,
            "address": None,   # 도로명·건물번호 코드만 제공 → 주소 변환은 다음 단계
            "designated": x.get("longTermPeribRgtDt"),
            **ltc_record(d),
        })
    return out


def provider_institutions(rows, prefix, table):
    by = {}
    for r in rows:
        if not (r.get("signguName") or "").startswith(prefix) or r.get("serviceTypeName") not in CARE_TYPES:
            continue
        pid = r.get("providerId")
        addr = " ".join(p for p in (r.get("loadAddress") or r.get("address"), r.get("loadAddressDetail") or r.get("addressDetail")) if p) or None
        g, how = guess_gu(f'{r.get("signguName") or ""} {addr or ""}', table)
        e = by.setdefault(pid, {
            "id": f"ssv:{pid}", "source": "사회서비스 제공기관", "name": r.get("providerName"),
            "services": [], "service_types": [], "gu": g, "gu_from": how,
            "address": addr, "zip": r.get("loadZip") or r.get("zip") or None,
            "tel": tel(r.get("telNumber")),
        })
        for k, v in (("services", r.get("serviceName")), ("service_types", r.get("serviceTypeName"))):
            if v and v not in e[k]:
                e[k].append(v)
    return list(by.values())


def pick_key(r, *pats):
    """표준데이터 필드 이름이 문서와 다를 수 있어, 이름 조각으로 찾는다."""
    for pat in pats:
        for k, v in r.items():
            if re.search(pat, k, re.I) and v:
                return v
    return None


def dementia_centers(rows, region_name, table):
    out = []
    for r in rows or []:
        addr = pick_key(r, r"^rdnmadr$", r"rdnm", r"adres|addr", r"lnmadr") or ""
        if region_name not in addr:
            continue
        g, how = guess_gu(addr, table)
        out.append({
            "id": f"dmc:{h([r.get('imbcltyCnterNm'), addr])}", "source": "치매안심센터",
            "name": pick_key(r, r"cnterNm$", r"Nm$"), "services": ["치매 상담·검사"],
            "gu": g, "gu_from": how, "address": addr or None,
            "tel": tel(pick_key(r, r"operInstt.*Telno", r"Telno", r"phone")),
            "programs": [p.strip() for p in re.split(r"[,\n/]+", pick_key(r, r"Prgm", r"prgrm") or "") if p.strip()] or None,
            "lat": pick_key(r, r"^latitude$", r"lat"), "lng": pick_key(r, r"^longitude$", r"lo?ng"),
        })
    return out


def ltc_gu_from_names(ltc, providers):
    """장기요양 지역코드 → 구: 같은 이름의 제공기관(구를 아는)으로 다수결."""
    known = {norm_name(p["name"]): p["gu"] for p in providers if p.get("gu")}
    votes = defaultdict(Counter)
    for x in ltc:
        g = known.get(norm_name(x["name"]))
        if g:
            votes[x["area_code"]][g] += 1
    table = {}
    for code, c in votes.items():
        (g, n), total = c.most_common(1)[0], sum(c.values())
        if n >= 2 and n / total >= 0.8:
            table[code] = g
    for x in ltc:
        if x["area_code"] in table:
            x["gu"], x["gu_from"] = table[x["area_code"]], "같은 이름 제공기관"
    return table


# ── 제도 ─────────────────────────────────────────────────────────────────

def program(d, scope, fallback):
    d = d or {}
    pick = lambda *ks: next((d.get(k) for k in ks if d.get(k)), None)  # noqa: E731
    return {
        "id": pick("servId") or fallback.get("servId"), "scope": scope,
        "name": pick("servNm") or fallback.get("servNm"),
        "dept": pick("jurMnofNm", "bizChrDeptNm") or fallback.get("jurOrgNm"),
        "summary": pick("wlfareInfoOutlCn", "servDgst") or fallback.get("servDgst"),
        "target": pick("tgtrDtlCn", "sprtTrgtCn"), "criteria": pick("slctCritCn"),
        "benefit": pick("alwServCn"), "apply": pick("aplyMtdCn"),
        "contact": pick("rprsCtadr") or fallback.get("rprsCtadr"), "year": pick("crtrYr"),
        "cycle": pick("sprtCycNm") or fallback.get("sprtCycNm"),
        "provision": pick("srvPvsnNm") or fallback.get("srvPvsnNm"),
        "online": fallback.get("onapPsbltYn"),
        "life": pick("lifeArray", "lifeNmArray") or fallback.get("lifeArray") or fallback.get("lifeNmArray"),
        "targets": pick("trgterIndvdlArray", "trgterIndvdlNmArray") or fallback.get("trgterIndvdlArray"),
        "themes": pick("intrsThemaArray", "intrsThemaNmArray") or fallback.get("intrsThemaArray"),
        "last_modified": fallback.get("lastModYmd") or pick("lastModYmd"),
        "link": fallback.get("servDtlLink"),
        "has_detail": bool(d),
    }


def link_programs(progs, inst):
    """제도 이름과 제공기관 서비스 이름을 맞춰 '이걸 제공하는 곳 N곳'을 단다."""
    by_service = defaultdict(set)
    for x in inst:
        for s in x.get("services") or []:
            by_service[re.sub(r"서비스$", "", s)].add(x["id"])
    for p in progs:
        key = re.sub(r"서비스$", "", p.get("name") or "")
        ids = by_service.get(key, set())
        p["provider_count"] = len(ids) or None
        p["provider_service"] = key if ids else None


# ── 실행 ─────────────────────────────────────────────────────────────────

def fill(rows, keys):
    return {k: sum(1 for x in rows if x.get(k)) for k in keys}


def diff(prev, cur):
    p, c = prev or {}, cur
    return {"added": sorted(set(c) - set(p)), "removed": sorted(set(p) - set(c)),
            "changed": sorted(k for k in set(c) & set(p) if c[k] != p[k])}


def main():
    ltc_s, ltc_d = latest("ltc")
    prov_s, prov_d = latest("providers")
    wel_s, wel_d = latest("welfare")
    std_s, std_d = latest("standard")
    events = {"date": today(), "regions": {}}
    for rk, r in regions().items():
        prefix = r["provider_signgu_prefix"]
        prov_rows = (prov_s or {}).get("rows", [])
        table = learn_gu(prov_rows, prefix)

        ltc = ltc_institutions(ltc_s, rk) if ltc_s else []
        prov = provider_institutions(prov_rows, prefix, table)
        dmc = dementia_centers((std_s or {}).get("dementia_center"), r["name"], table)
        code_gu = ltc_gu_from_names(ltc, prov)
        inst = ltc + prov + dmc

        progs = []
        if wel_s:
            nd = wel_s["national"]["detail"]
            progs += [program(nd.get(x.get("servId")), "national", x) for x in wel_s["national"]["list"]]
            ld = wel_s["local"]["detail"]
            progs += [program(ld.get(x.get("servId")), "local", x) for x in wel_s["local"]["list"].get(rk, [])]
        link_programs(progs, inst)

        out = DATA / "site" / "data" / rk
        write_json(out / "institutions.json", inst)
        write_json(out / "programs.json", progs)
        meta = {
            "region": r["name"], "built": today(),
            "sources": {"장기요양기관": ltc_d, "사회서비스 제공기관": prov_d, "복지서비스": wel_d, "표준데이터": std_d},
            "counts": {"institutions": len(inst), "programs": len(progs),
                       **Counter(x["source"] for x in inst), "구": dict(Counter(x.get("gu") or "모름" for x in inst))},
            "fill": {
                "장기요양기관": fill(ltc, ("tel", "gu", "capacity", "fees", "programs", "staff", "rooms", "homepage", "transport", "parking", "floor")),
                "사회서비스 제공기관": fill(prov, ("tel", "gu", "address")),
                "치매안심센터": fill(dmc, ("tel", "gu", "address", "programs")),
                "제도": fill(progs, ("has_detail", "target", "criteria", "benefit", "apply", "contact", "year", "provider_count")),
            },
            "learned": {"구 사전 토큰 수": len(table), "장기요양 지역코드→구": code_gu},
            "standard_fields": {k: sorted((v or [{}])[0].keys()) for k, v in (std_s or {}).items()},
        }
        write_json(out / "meta.json", meta)

        state_p = DATA / "state" / f"built_{rk}.json"
        prev = read_json(state_p, {})
        cur = {"institutions": {x["id"]: h(x) for x in inst}, "programs": {x["id"]: h(x) for x in progs if x["id"]}}
        events["regions"][rk] = {
            "institutions": diff(prev.get("institutions"), cur["institutions"]) if prev else {"first_build": True},
            "programs": diff(prev.get("programs"), cur["programs"]) if prev else {"first_build": True},
        }
        write_json(state_p, cur)
        log(f"{r['name']}: 기관 {len(inst)}, 제도 {len(progs)}", json.dumps(meta["fill"], ensure_ascii=False))

    write_json(DATA / "events" / f"{today()}.json", events)
    write_json(DATA / "site" / "data" / "events-latest.json", events)
    return events

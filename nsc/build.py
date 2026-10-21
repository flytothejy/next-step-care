"""가장 최근 수집본으로 지역별 화면 데이터를 만들고, 지난번과 달라진 점을 기록한다.

출력
  site/data/<지역>/institutions.json   기관 (장기요양 + 사회서비스 제공기관)
  site/data/<지역>/programs.json       제도 (중앙부처 + 해당 지자체 복지서비스)
  site/data/<지역>/meta.json           건수, 원천별 수집일, 기준 시각
  events/<날짜>.json                   추가·삭제·변경 (알림·주간 검토용)
"""
import hashlib
import json
import re

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


def latest(name):
    snaps = sorted((DATA / "snap").glob(f"*/{name}.json.gz"))
    if not snaps:
        return None, None
    return read_json(snaps[-1]), snaps[-1].parent.name


def tel(s):
    s = (s or "").strip()
    return None if not s or PLACEHOLDER_TEL.search(s) else s


def h(obj):
    return hashlib.sha1(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def as_list(v):
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


def ltc_institutions(snap, rk):
    out = []
    detail = snap.get("detail", {})
    for x in snap.get("list", {}).get(rk, []):
        sym = x.get("longTermAdminSym")
        d = detail.get(sym) or {}
        kind, verified = LTC_KIND.get(x.get("adminPttnCd"), (None, False))
        t = "-".join(p for p in (d.get("locTelNo_1"), d.get("locTelNo_2"), d.get("locTelNo_3")) if p)
        out.append({
            "id": f"ltc:{sym}", "source": "장기요양기관", "name": x.get("adminNm"),
            "kind_code": x.get("adminPttnCd"), "kind": kind, "kind_verified": verified,
            "services": [kind] if kind else [],
            "area_code": f'{x.get("siDoCd")}{x.get("siGunGuCd")}', "gu": None,
            "address": None,   # 상세는 도로명·건물번호 코드만 제공 → 주소 변환은 다음 단계
            "addr_codes": {k: d.get(k) for k in ("roadNmCd", "gunmulMlno", "gunmulSlno", "hmPostNo", "BDongCd", "HDongCd") if d.get(k)},
            "tel": tel(t), "designated": x.get("longTermPeribRgtDt"),
        })
    return out


def provider_institutions(snap, prefix):
    by = {}
    for r in snap.get("rows", []):
        if not (r.get("signguName") or "").startswith(prefix) or r.get("serviceTypeName") not in CARE_TYPES:
            continue
        pid = r.get("providerId")
        e = by.setdefault(pid, {
            "id": f"ssv:{pid}", "source": "사회서비스 제공기관", "name": r.get("providerName"),
            "services": [], "service_types": [],
            "gu": (r.get("signguName") or "")[len(prefix):].strip() or None,
            "address": " ".join(p for p in (r.get("loadAddress") or r.get("address"), r.get("loadAddressDetail") or r.get("addressDetail")) if p) or None,
            "zip": r.get("loadZip") or r.get("zip") or None,
            "tel": tel(r.get("telNumber")),
        })
        for k, v in (("services", r.get("serviceName")), ("service_types", r.get("serviceTypeName"))):
            if v and v not in e[k]:
                e[k].append(v)
    return list(by.values())


def program(d, scope, fallback):
    d = d or {}
    pick = lambda *ks: next((d.get(k) for k in ks if d.get(k)), None)  # noqa: E731
    return {
        "id": pick("servId") or fallback.get("servId"), "scope": scope,
        "name": pick("servNm") or fallback.get("servNm"),
        "dept": pick("jurMnofNm", "bizChrDeptNm") or fallback.get("jurOrgNm"),
        "summary": pick("wlfareInfoOutlCn", "servDgst"),
        "target": pick("tgtrDtlCn", "sprtTrgtCn"), "criteria": pick("slctCritCn"),
        "benefit": pick("alwServCn"), "apply": pick("aplyMtdCn"),
        "contact": pick("rprsCtadr"), "year": pick("crtrYr"),
        "life": pick("lifeArray", "lifeNmArray") or fallback.get("lifeArray") or fallback.get("lifeNmArray"),
        "targets": pick("trgterIndvdlArray", "trgterIndvdlNmArray"),
        "themes": pick("intrsThemaArray", "intrsThemaNmArray"),
        "last_modified": fallback.get("lastModYmd") or pick("lastModYmd"),
        "link": fallback.get("servDtlLink"),
    }


def diff(prev, cur):
    p, c = prev or {}, cur
    return {"added": sorted(set(c) - set(p)), "removed": sorted(set(p) - set(c)),
            "changed": sorted(k for k in set(c) & set(p) if c[k] != p[k])}


def main():
    ltc_s, ltc_d = latest("ltc")
    prov_s, prov_d = latest("providers")
    wel_s, wel_d = latest("welfare")
    events = {"date": today(), "regions": {}}
    for rk, r in regions().items():
        inst = []
        if ltc_s:
            inst += ltc_institutions(ltc_s, rk)
        if prov_s:
            inst += provider_institutions(prov_s, r["provider_signgu_prefix"])
        progs = []
        if wel_s:
            nd = wel_s["national"]["detail"]
            progs += [program(nd.get(x.get("servId")), "national", x) for x in wel_s["national"]["list"]]
            ld = wel_s["local"]["detail"]
            progs += [program(ld.get(x.get("servId")), "local", x) for x in wel_s["local"]["list"].get(rk, [])]

        out = DATA / "site" / "data" / rk
        write_json(out / "institutions.json", inst)
        write_json(out / "programs.json", progs)
        meta = {"region": r["name"], "built": today(),
                "sources": {"장기요양기관": ltc_d, "사회서비스 제공기관": prov_d, "복지서비스": wel_d},
                "counts": {"institutions": len(inst), "programs": len(progs),
                           "장기요양기관": sum(1 for x in inst if x["source"] == "장기요양기관"),
                           "사회서비스 제공기관": sum(1 for x in inst if x["source"] == "사회서비스 제공기관"),
                           "전화번호 있음": sum(1 for x in inst if x["tel"]),
                           "주소 있음": sum(1 for x in inst if x["address"])}}
        write_json(out / "meta.json", meta)

        state_p = DATA / "state" / f"built_{rk}.json"
        prev = read_json(state_p, {})
        cur = {"institutions": {x["id"]: h(x) for x in inst}, "programs": {x["id"]: h(x) for x in progs if x["id"]}}
        events["regions"][rk] = {
            "institutions": diff(prev.get("institutions"), cur["institutions"]) if prev else {"first_build": True},
            "programs": diff(prev.get("programs"), cur["programs"]) if prev else {"first_build": True},
        }
        write_json(state_p, cur)
        log(f"{r['name']}: 기관 {len(inst)}, 제도 {len(progs)}", meta["counts"])

    write_json(DATA / "events" / f"{today()}.json", events)
    write_json(DATA / "site" / "data" / "events-latest.json", events)
    return events

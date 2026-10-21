"""주소·좌표 채우기 (행정안전부 도로명주소 API). 결과는 state/geocode.json 에 쌓고 새 것만 묻는다.

브이월드는 해외 IP(GitHub Actions)를 막아 수집 서버에서 쓸 수 없다. juso.go.kr 은 열려 있다.

  검색 API  (JUSO_KEY)        주소 글자 → 도로명주소, 행정구역코드(admCd), 도로명코드(rnMgtSn), 건물번호
  좌표 API  (JUSO_COORD_KEY)  admCd + rnMgtSn + 지하여부 + 건물 본·부번 → 출입구 좌표 (UTM-K → 위경도로 변환)

순서
  1. 구: 장기요양 도로명코드 앞 5자리(시군구 코드)를 가장 먼저 믿는다
  2. 주소 글자가 있으면 검색 → 정리된 도로명주소·구. 결과로 '도로명코드 → 도로 이름·행정구역코드'를 배운다
  3. 도로명코드·건물번호만 있으면 (장기요양 상세) 배운 도로 이름으로 주소를 만들어 검색,
     도로명코드·건물번호가 같을 때만 채택. 도로를 모르면 법정동 코드 후보(시군구 5 + 읍면동 3 + 리 2)만 만든다
  4. 좌표 API로 출입구 좌표
검색 키만 있어도 1~3은 된다. 키가 없으면 캐시만 쓰고 넘어간다.
"""
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

from .core import DATA, UA, log, read_json, today, write_json

SEARCH = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
COORD = "https://business.juso.go.kr/addrlink/addrCoordApi.do"
CACHE_P = DATA / "state" / "geocode.json"
RETRY_MISS_DAYS = 30
MAX_CALLS = int(os.environ.get("NSC_GEO_MAX_CALLS", "4000"))
RI_TRY = [f"{i:02d}" for i in range(21, 46)]     # 읍·면의 리 코드 후보 (00 다음)
FATAL = {"E0001", "E0014", "E0015"}              # 승인되지 않은 키, 기간 만료 등

ROAD_TAIL = re.compile(r"^(.*?[가-힣0-9·]+(?:로|길)\s*\d+(?:-\d+)?)")
PAREN = re.compile(r"\([^)]*\)")
ROAD_NAME = re.compile(r"([가-힣0-9·]+(?:로|길))\s*(\d+)(?:-(\d+))?")


def _keys():
    return os.environ.get("JUSO_KEY", ""), os.environ.get("JUSO_COORD_KEY", "")


def _redact(s):
    for k in _keys():
        if k:
            s = s.replace(k, "***")
    return s


# ── 좌표계: UTM-K (EPSG:5179, GRS80 횡메르카토르) → WGS84 위경도 ──
_A, _F = 6378137.0, 1 / 298.257222101
_K0, _LAT0, _LON0, _FE, _FN = 0.9996, math.radians(38), math.radians(127.5), 1_000_000.0, 2_000_000.0
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)


def _m(phi):
    e2, e4, e6 = _E2, _E2 ** 2, _E2 ** 3
    return _A * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                 - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
                 + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
                 - (35 * e6 / 3072) * math.sin(6 * phi))


def utmk_to_wgs84(x, y):
    m = _m(_LAT0) + (y - _FN) / _K0
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2 ** 2 / 64 - 5 * _E2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    p1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
          + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
          + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    c1 = _EP2 * math.cos(p1) ** 2
    t1 = math.tan(p1) ** 2
    n1 = _A / math.sqrt(1 - _E2 * math.sin(p1) ** 2)
    r1 = _A * (1 - _E2) / (1 - _E2 * math.sin(p1) ** 2) ** 1.5
    d = (x - _FE) / (n1 * _K0)
    lat = p1 - (n1 * math.tan(p1) / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * _EP2) * d ** 4 / 24
                                          + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * _EP2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = _LON0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                   + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * _EP2 + 24 * t1 ** 2) * d ** 5 / 120) / math.cos(p1)
    return round(math.degrees(lat), 6), round(math.degrees(lon), 6)


class Geo:
    def __init__(self):
        self.cache = read_json(CACHE_P, {}) or {}
        self.calls = 0
        self.errors = Counter()
        sk, ck = _keys()
        self.online = {"search": bool(sk), "coord": bool(ck)}
        self.failed = False

    def _req(self, kind, params):
        if not self.online[kind] or self.calls >= MAX_CALLS:
            self.failed = True
            return None
        sk, ck = _keys()
        q = urllib.parse.urlencode({"confmKey": sk if kind == "search" else ck, "resultType": "json", **params})
        for i in range(3):
            self.calls += 1
            try:
                req = urllib.request.Request(f"{SEARCH if kind == 'search' else COORD}?{q}", headers=UA)
                with urllib.request.urlopen(req, timeout=20) as r:
                    res = json.loads(r.read().decode("utf-8", "replace")).get("results", {})
                code = res.get("common", {}).get("errorCode", "")
                if code == "0":
                    return res.get("juso") or []
                self.errors[code] += 1
                if code in FATAL:
                    log(f"주소 API({kind}) 중단:", code, _redact(res.get("common", {}).get("errorMessage", ""))[:120])
                    self.online[kind] = False
                    self.failed = True
                    return None
                if code == "-999":        # 시스템 오류 → 재시도
                    time.sleep(1 + i)
                    continue
                return []                 # 입력 오류 등: 결과 없음으로 본다
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
                self.errors[type(e).__name__] += 1
                time.sleep(1 + i)
        self.failed = True
        return None

    def _cached(self, ck, fn):
        hit = self.cache.get(ck)
        if hit and (hit.get("ok") or _days_since(hit.get("at")) < RETRY_MISS_DAYS):
            return hit if hit.get("ok") else None
        self.failed = False
        res = fn()
        if res is None and self.failed:          # 오류·한도·키 없음으로 못 물어본 것은 '없음'으로 남기지 않음
            return hit if hit and hit.get("ok") else None
        self.cache[ck] = {**(res or {}), "ok": bool(res), "at": today()}
        return self.cache[ck] if res else None

    # ── 호출 단위 ──
    def search(self, addr):
        a = clean(addr)
        if not a:
            return None

        def ask():
            rows = self._req("search", {"currentPage": "1", "countPerPage": "5", "keyword": a})
            if not rows:
                return None
            j = rows[0]
            return {k: j.get(k) for k in ("roadAddrPart1", "roadAddr", "jibunAddr", "zipNo", "admCd", "rnMgtSn", "rn", "siNm",
                                         "udrtYn", "buldMnnm", "buldSlno", "sggNm", "emdNm", "bdNm")}
        return self._cached(f"s:{a}", ask)

    def coord(self, adm, rn, udrt, mn, sn):
        def ask():
            rows = self._req("coord", {"admCd": adm, "rnMgtSn": rn, "udrtYn": udrt or "0",
                                       "buldMnnm": str(int(mn)), "buldSlno": str(int(sn or 0))})
            if not rows or not rows[0].get("entX"):
                return None
            lat, lng = utmk_to_wgs84(float(rows[0]["entX"]), float(rows[0]["entY"]))
            return {"lat": lat, "lng": lng, "bdNm": rows[0].get("bdNm")}
        return self._cached(f"c:{adm}:{rn}:{udrt or 0}:{int(mn)}:{int(sn or 0)}", ask)

    def save(self):
        write_json(CACHE_P, self.cache)


# ── 도움 함수 ──

def _days_since(d):
    import datetime as dt
    try:
        return (dt.date.fromisoformat(today()) - dt.date.fromisoformat(d)).days
    except (TypeError, ValueError):
        return 10 ** 6


def clean(addr):
    """'경기도 화성시 동탄순환대로24길  101 상가2동103호 (영천동)' → '경기도 화성시 동탄순환대로24길 101'"""
    if not addr:
        return None
    a = PAREN.sub(" ", addr).split(",")[0]
    a = re.sub(r"\s+", " ", a).strip()
    m = ROAD_TAIL.match(a)
    return m.group(1) if m else a


def gu_of(text, gu_re):
    m = gu_re.search(text or "")
    return m.group(1) if m else None


# ── 적용 ──

def apply(inst, region, gu_re):
    """기관 목록에 gu, address, lat/lng, geo_from 을 채운다. 반환: 요약."""
    g = Geo()
    gu_codes = region.get("gu_codes", {})
    prefix = region["provider_signgu_prefix"]
    city = region["name"]
    how = Counter()
    roads = {}          # rnMgtSn → (admCd, 도로 이름, 시군구 이름)

    def take(x, r, src):
        """검색 결과를 기관에 붙인다: 정리된 도로명주소, 좌표 API 입력값, 구."""
        x["road_address"] = r.get("roadAddrPart1")
        if not x.get("address"):
            x["address"], x["address_from"] = r.get("roadAddrPart1"), src
        x["_juso"] = (r.get("admCd"), r.get("rnMgtSn"), r.get("udrtYn"), r.get("buldMnnm"), r.get("buldSlno"))
        if city not in (r.get("roadAddrPart1") or ""):
            # 화성시에서 서비스하지만 사무실은 다른 지역 (방문형 서비스에 많다)
            x["office_outside"] = " ".join(p for p in (r.get("siNm"), r.get("sggNm")) if p) or True
            how["사무실 다른 지역"] += 1
        elif not x.get("gu"):
            gu = gu_of(r.get("sggNm"), gu_re) or gu_of(r.get("roadAddrPart1"), gu_re)
            if gu:
                x["gu"], x["gu_from"] = gu, "주소 검색"
        how[f"주소: {src}"] += 1

    # 1. 구: 도로명코드의 시군구 코드가 가장 확실하다
    for x in inst:
        rn = (x.get("addr_codes") or {}).get("roadNmCd") or ""
        if rn[:5] in gu_codes:
            if x.get("gu") and x["gu"] != gu_codes[rn[:5]] and x.get("gu_from") != "도로명코드":
                how["구 바로잡음"] += 1
            x["gu"], x["gu_from"] = gu_codes[rn[:5]], "도로명코드"

    # 2. 주소 글자가 있으면 검색. 결과로 '도로명코드 → 도로 이름·행정구역코드'를 배운다.
    for x in inst:
        r = g.search(x["address"]) if x.get("address") else None
        if r and r.get("rnMgtSn"):
            roads.setdefault(r["rnMgtSn"], (r.get("admCd"), r.get("rn")))
            take(x, r, "검색")

    # 3. 코드만 있는 곳: 배운 도로 이름으로 주소를 만들어 검색하고, 도로명코드·건물번호가 같을 때만 채택
    for x in inst:
        codes = x.get("addr_codes") or {}
        rn, mn, sn = codes.get("roadNmCd"), codes.get("gunmulMlno"), int(codes.get("gunmulSlno") or 0)
        if x.get("_juso") or not (rn and mn and str(mn).isdigit()):
            continue
        if rn in roads and roads[rn][1]:
            r = g.search(f"{prefix} {roads[rn][1]} {int(mn)}" + (f"-{sn}" if sn else ""))
            if r and r.get("rnMgtSn") == rn and int(r.get("buldMnnm") or -1) == int(mn) and int(r.get("buldSlno") or 0) == sn:
                take(x, r, "도로명코드")
                continue
        # 도로 이름을 모르면 좌표 API용 후보만 만든다 (법정동 코드 = 시군구 5 + 읍면동 3 + 리 2)
        cands = [roads[rn][0]] if rn in roads and roads[rn][0] else []
        if codes.get("BDongCd"):
            base = f"{rn[:5]}{int(codes['BDongCd']):03d}"
            cands += [base + "00"] + ([base + ri for ri in RI_TRY] if int(codes["BDongCd"]) >= 250 else [])
        x["_cands"] = (list(dict.fromkeys(cands)), rn, mn, sn)

    # 4. 좌표 (좌표제공 API)
    for x in inst:
        j, cands = x.pop("_juso", None), x.pop("_cands", None)
        if x.get("lat") and x.get("lng"):
            how["좌표: 원천"] += 1
            continue
        c = None
        if j and all(j[k] for k in (0, 1, 3)):
            c = g.coord(j[0], j[1], j[2], j[3], j[4])
        elif cands and cands[0]:
            adms, rn, mn, sn = cands
            for adm, udrt in [(a, "0") for a in adms] + [(adms[0], "1")]:   # 지하 건물은 드물어 첫 후보만
                c = g.coord(adm, rn, udrt, mn, sn)
                if c:
                    break
        if c:
            x["lat"], x["lng"], x["geo_from"] = c["lat"], c["lng"], "도로명주소 출입구"
            how["좌표: 변환"] += 1
        else:
            how["좌표: 없음"] += 1

    g.save()
    sk, ck = _keys()
    summary = {"calls": g.calls, "keys": {"search": bool(sk), "coord": bool(ck)}, "errors": dict(g.errors),
               "roads": len(roads), "how": dict(how), "cache": len(g.cache)}
    log("좌표:", json.dumps(summary, ensure_ascii=False))
    return summary

"""주소·좌표 채우기 (브이월드). 결과는 state/geocode.json 에 쌓아 두고 새 주소만 묻는다.

기관마다 가진 단서가 달라 순서대로 시도한다.
  1. 주소 글자가 있으면            → 주소 → 좌표 (도로명, 안 되면 지번)
  2. 도로명코드·건물번호만 있으면   → 도로명주소 건물 레이어에서 코드로 바로 찾기
                                   → 다른 기관에서 배운 '도로명코드 → 도로 이름'으로 주소를 만들어 1번
                                   → 기관 이름으로 장소 검색, 건물번호·시군구가 맞을 때만 채택
구(區)는 장기요양 도로명코드 앞 5자리(시군구 코드)가 있으면 그것을 가장 먼저 믿는다.

키: VWORLD_KEY (GitHub Secrets). 없으면 캐시만 쓰고 넘어간다.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

from .core import DATA, UA, log, read_json, today, write_json

API = "https://api.vworld.kr/req/"
CACHE_P = DATA / "state" / "geocode.json"
RETRY_MISS_DAYS = 30
MAX_CALLS = int(os.environ.get("NSC_GEO_MAX_CALLS", "3000"))

ROAD_TAIL = re.compile(r"^(.*?[가-힣0-9·]+(?:로|길)\s*\d+(?:-\d+)?)")
PAREN = re.compile(r"\([^)]*\)")
ROAD_NAME = re.compile(r"([가-힣0-9·]+(?:로|길))\s*(\d+)(?:-(\d+))?")


def _key():
    return os.environ.get("VWORLD_KEY", "")


def _redact(s):
    k = _key()
    return s.replace(k, "***") if k else s


class Geo:
    def __init__(self):
        self.cache = read_json(CACHE_P, {}) or {}
        self.calls = 0
        self.errors = Counter()
        self.online = bool(_key())
        self.failed = False

    # ── 호출 ──
    def _req(self, kind, params):
        if not self.online or self.calls >= MAX_CALLS:
            self.failed = True
            return None
        extra = {"domain": os.environ["VWORLD_DOMAIN"]} if os.environ.get("VWORLD_DOMAIN") else {}
        q = urllib.parse.urlencode({"key": _key(), "format": "json", **extra, **params})
        for i in range(3):
            self.calls += 1
            try:
                req = urllib.request.Request(f"{API}{kind}?{q}", headers=UA)
                with urllib.request.urlopen(req, timeout=20) as r:
                    body = json.loads(r.read().decode("utf-8", "replace"))
                resp = body.get("response", {})
                st = resp.get("status")
                if st == "ERROR":
                    err = resp.get("error", {})
                    code = err.get("code", "")
                    self.errors[code] += 1
                    self.failed = True
                    if code in ("INVALID_KEY", "INCORRECT_KEY", "UNAVAILABLE_KEY", "OVER_REQUEST_LIMIT"):
                        log("브이월드 중단:", code, _redact(err.get("text", ""))[:120])
                        self.online = False
                    return None
                return resp
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
                self.errors[type(e).__name__] += 1
                time.sleep(1 + i)
        self.failed = True
        return None

    def _cached(self, ck, fn):
        hit = self.cache.get(ck)
        if hit and (hit.get("ok") or _days_since(hit.get("at")) < RETRY_MISS_DAYS):
            return hit if hit.get("ok") else None
        if not self.online:
            return hit if hit and hit.get("ok") else None
        self.failed = False
        res = fn()
        if res is None and self.failed:          # 오류·한도로 못 물어본 것은 '없음'으로 남기지 않음
            return None
        self.cache[ck] = {**(res or {}), "ok": bool(res), "at": today()}
        return self.cache[ck] if res else None

    # ── 전략 ──
    def address(self, addr):
        a = clean(addr)
        if not a:
            return None

        def ask():
            for typ in ("road", "parcel"):
                r = self._req("address", {"service": "address", "request": "getcoord", "version": "2.0",
                                          "crs": "epsg:4326", "refine": "true", "simple": "false",
                                          "type": typ, "address": a})
                if r and r.get("status") == "OK":
                    p = r["result"]["point"]
                    ref = r.get("refined", {})
                    return {"lat": round(float(p["y"]), 6), "lng": round(float(p["x"]), 6),
                            "addr": ref.get("text") or a, "level2": ref.get("structure", {}).get("level2"),
                            "how": f"주소({'도로명' if typ == 'road' else '지번'})"}
            return None
        return self._cached(f"a:{a}", ask)

    def building(self, codes):
        """도로명코드(12) + 건물 본·부번 → 도로명주소 건물 레이어."""
        rn, mn = codes.get("roadNmCd"), codes.get("gunmulMlno")
        if not rn or len(rn) != 12 or not mn:
            return None
        sn = codes.get("gunmulSlno") or "0"

        def ask():
            flt = f"sig_cd:=:{rn[:5]}|rn_cd:=:{rn[5:]}|buld_mnnm:=:{int(mn)}|buld_slno:=:{int(sn)}"
            r = self._req("data", {"service": "data", "request": "GetFeature", "version": "2.0",
                                   "data": "LT_C_SPBD", "attrFilter": flt, "geometry": "true",
                                   "attribute": "true", "crs": "EPSG:4326", "size": "5"})
            feats = (((r or {}).get("result") or {}).get("featureCollection") or {}).get("features") or []
            if not feats:
                return None
            f = feats[0]
            lat, lng = centroid(f.get("geometry"))
            if lat is None:
                return None
            pr = f.get("properties", {})
            road = pr.get("rd_nm") or pr.get("rn")
            num = f"{int(mn)}" + (f"-{int(sn)}" if int(sn) else "")
            return {"lat": lat, "lng": lng, "addr": f"{road} {num}" if road else None,
                    "road": road, "how": "건물(도로명코드)"}
        return self._cached(f"b:{rn}:{mn}:{sn}", ask)

    def place(self, name, region, codes):
        """기관 이름 검색. 같은 시 안이고 건물번호가 맞을 때만 받는다."""
        mn = str(int(codes["gunmulMlno"])) if codes.get("gunmulMlno") else None
        sn = int(codes.get("gunmulSlno") or 0)

        def ask():
            r = self._req("search", {"service": "search", "request": "search", "version": "2.0",
                                     "crs": "EPSG:4326", "size": "10", "page": "1", "type": "place",
                                     "query": f"{region} {name}"})
            items = (((r or {}).get("result") or {}).get("items")) or []
            for it in items:
                road = (it.get("address") or {}).get("road") or ""
                if region not in road:
                    continue
                m = ROAD_NAME.search(road)
                if mn and not (m and m.group(2) == mn and int(m.group(3) or 0) == sn):
                    continue
                p = it.get("point") or {}
                return {"lat": round(float(p["y"]), 6), "lng": round(float(p["x"]), 6),
                        "addr": road, "how": "이름 검색(건물번호 일치)" if mn else "이름 검색"}
            return None
        return self._cached(f"p:{region}:{name}:{mn}:{sn}", ask)

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


def centroid(geom):
    """폴리곤(멀티 포함) 외곽선 꼭짓점 평균. 건물 하나 크기라 이 정도로 충분하다."""
    if not geom:
        return None, None
    c = geom.get("coordinates")
    t = geom.get("type")
    if t == "Point":
        return round(c[1], 6), round(c[0], 6)
    ring = c[0] if t == "Polygon" else c[0][0] if t == "MultiPolygon" else None
    if not ring:
        return None, None
    xs, ys = zip(*[(p[0], p[1]) for p in ring])
    return round(sum(ys) / len(ys), 6), round(sum(xs) / len(xs), 6)


def learn_roads(rows):
    """주소 글자와 도로명코드를 함께 가진 기관에서 '도로명코드 → 도로 이름'을 배운다."""
    tab = defaultdict(Counter)
    for x in rows:
        c = (x.get("addr_codes") or {}).get("roadNmCd")
        m = ROAD_NAME.search(clean(x.get("address")) or "")
        if c and m:
            tab[c][m.group(1)] += 1
    return {c: n.most_common(1)[0][0] for c, n in tab.items()}


def gu_of(text, gu_re):
    m = gu_re.search(text or "")
    return m.group(1) if m else None


# ── 적용 ──

def apply(inst, region, gu_re):
    """기관 목록에 gu(코드), address, lat/lng, geo_from 을 채운다. 반환: 요약."""
    g = Geo()
    gu_codes = region.get("gu_codes", {})
    city = region["name"]
    prefix = region["provider_signgu_prefix"]
    roads = learn_roads([x for x in inst if x.get("source") == "장기요양기관"])
    how = Counter()

    for x in inst:
        codes = x.get("addr_codes") or {}
        rn = codes.get("roadNmCd") or ""
        # 구: 도로명코드의 시군구 코드가 가장 확실하다
        if rn[:5] in gu_codes and x.get("gu_from") != "도로명코드":
            if x.get("gu") and x["gu"] != gu_codes[rn[:5]]:
                how["구 바로잡음"] += 1
            x["gu"], x["gu_from"] = gu_codes[rn[:5]], "도로명코드"

        if x.get("lat") and x.get("lng"):
            how["원천 좌표"] += 1
            continue

        r = None
        if x.get("address"):
            r = g.address(x["address"])
        if not r and codes:
            r = g.building(codes)
            if not r and rn in roads and codes.get("gunmulMlno"):
                sn = int(codes.get("gunmulSlno") or 0)
                guess = f"{prefix} {roads[rn]} {int(codes['gunmulMlno'])}" + (f"-{sn}" if sn else "")
                r = g.address(guess)
                if r:
                    r = {**r, "how": "도로명코드로 만든 주소"}
            if not r and x.get("name"):
                r = g.place(x["name"], city, codes)
        if not r:
            how["못 찾음"] += 1
            continue
        x["lat"], x["lng"], x["geo_from"] = r["lat"], r["lng"], r["how"]
        if not x.get("address") and r.get("addr"):
            a = r["addr"]
            x["address"] = a if a.startswith(("경기", "서울")) else f"{prefix} {a}"
            x["address_from"] = r["how"]
        if not x.get("gu"):
            gu = gu_of(r.get("level2"), gu_re) or gu_of(x.get("address"), gu_re)
            if gu:
                x["gu"], x["gu_from"] = gu, "좌표 변환"
        how[r["how"]] += 1

    g.save()
    summary = {"calls": g.calls, "online": bool(_key()), "errors": dict(g.errors),
               "learned_roads": len(roads), "how": dict(how), "cache": len(g.cache)}
    log("좌표:", json.dumps(summary, ensure_ascii=False))
    return summary

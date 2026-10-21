"""브이월드 좌표 변환 시험 (키는 출력하지 않음). 결과: probe/out/geo.json"""
import json, os, pathlib, urllib.parse, urllib.request

K = os.environ.get("VWORLD_KEY", "")
OUT = pathlib.Path("probe/out"); OUT.mkdir(parents=True, exist_ok=True)
res = {"has_key": bool(K)}

def call(kind, p, domain=None):
    q = {"key": K, "format": "json", **p}
    if domain:
        q["domain"] = domain
    try:
        with urllib.request.urlopen(f"https://api.vworld.kr/req/{kind}?{urllib.parse.urlencode(q)}", timeout=20) as r:
            t = r.read().decode("utf-8", "replace")
    except Exception as e:
        t = f"EXC {type(e).__name__}: {e}"
    return t.replace(K, "***")[:2500] if K else t[:2500]

if K:
    A = {"service": "address", "request": "getcoord", "version": "2.0", "crs": "epsg:4326",
         "refine": "true", "simple": "false", "type": "road"}
    res["addr_ltc"] = call("address", {**A, "address": "경기도 화성시 동탄순환대로24길 101"})
    res["addr_ltc_domain"] = call("address", {**A, "address": "경기도 화성시 동탄순환대로24길 101"}, "https://flytothejy.github.io")
    res["addr_prov"] = call("address", {**A, "address": "경기도 화성시 병점구 영통로60번길 7"})
    res["addr_eup"] = call("address", {**A, "address": "경기도 화성시 봉담읍 진등3길 11-21"})
    D = {"service": "data", "request": "GetFeature", "version": "2.0", "data": "LT_C_SPBD",
         "geometry": "true", "attribute": "true", "crs": "EPSG:4326", "size": "3"}
    # 415974852381 / 101 / 0  (동탄순환대로24길 101)
    res["bld_newsig"] = call("data", {**D, "attrFilter": "sig_cd:=:41597|rn_cd:=:4852381|buld_mnnm:=:101|buld_slno:=:0"})
    res["bld_oldsig"] = call("data", {**D, "attrFilter": "sig_cd:=:41590|rn_cd:=:4852381|buld_mnnm:=:101|buld_slno:=:0"})
    res["bld_rnonly"] = call("data", {**D, "attrFilter": "rn_cd:=:4852381|buld_mnnm:=:101"})
    S = {"service": "search", "request": "search", "version": "2.0", "crs": "EPSG:4326", "size": "5", "page": "1"}
    res["place"] = call("search", {**S, "type": "place", "query": "화성시 동탄효드림주간보호센터"})
    res["road_search"] = call("search", {**S, "type": "road", "query": "화성시 동탄순환대로24길"})
(OUT / "geo.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
print(json.dumps({k: (v[:300] if isinstance(v, str) else v) for k, v in res.items()}, ensure_ascii=False, indent=1))

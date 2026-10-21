"""실제 검색 키로 스냅샷 기관 전체에 geo.apply 를 돌려 본다. 결과: probe/out/geo_run.json"""
import collections, json, os, pathlib, subprocess, sys
sys.path.insert(0, ".")
os.environ.setdefault("NSC_GEO_MAX_CALLS", "3000")
from nsc import geo, build
from nsc.core import regions

subprocess.run(["git", "fetch", "-q", "origin", "snapshot/hwaseong"], check=True)
inst = json.loads(subprocess.run(["git", "show", "FETCH_HEAD:snapshot/hwaseong/institutions.json"],
                                 capture_output=True, text=True, check=True).stdout)
geo.CACHE_P = pathlib.Path("probe/out/geocode_cache.json")
s = geo.apply(inst, regions()["hwaseong"], build.GU)
out = {"summary": s}
for src in ("장기요양기관", "사회서비스 제공기관", "치매안심센터"):
    xs = [x for x in inst if x["source"] == src]
    out[src] = {"n": len(xs), "address": sum(1 for x in xs if x.get("address")),
                "road_address": sum(1 for x in xs if x.get("road_address")),
                "gu": sum(1 for x in xs if x.get("gu")), "lat": sum(1 for x in xs if x.get("lat")),
                "address_from": dict(collections.Counter(x.get("address_from") for x in xs))}
out["samples"] = [{k: x.get(k) for k in ("name", "address", "road_address", "address_from", "gu", "addr_codes")}
                  for x in inst if x.get("address_from") == "도로명코드"][:8]
out["unknown_road_codes"] = len({(x.get("addr_codes") or {}).get("roadNmCd") for x in inst
                                 if x["source"] == "장기요양기관" and not x.get("road_address")})
pathlib.Path("probe/out/geo_run.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
print(json.dumps(out, ensure_ascii=False, indent=1)[:3000])

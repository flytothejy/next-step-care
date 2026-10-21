"""도로명주소 검색·좌표 API 시험 (키는 출력하지 않음). 결과: probe/out/geo.json"""
import json, os, pathlib, urllib.parse, urllib.request

SK, CK = os.environ.get("JUSO_KEY", ""), os.environ.get("JUSO_COORD_KEY", "")
OUT = pathlib.Path("probe/out"); OUT.mkdir(parents=True, exist_ok=True)
res = {"keys": {"search": bool(SK), "coord": bool(CK)}}

def call(url, p):
    try:
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode({**p, 'resultType': 'json'})}", timeout=20) as r:
            t = r.read().decode("utf-8", "replace")
    except Exception as e:
        t = f"EXC {type(e).__name__}: {e}"
    for k in (SK, CK):
        if k: t = t.replace(k, "***")
    return t[:1500]

S = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
C = "https://business.juso.go.kr/addrlink/addrCoordApi.do"
for name, kw in [("ltc_dong", "경기도 화성시 동탄순환대로24길 101"), ("ltc_eup", "경기도 화성시 봉담읍 진등3길 11-21"),
                 ("prov", "경기도 화성시 병점구 영통로60번길 7"), ("ltc_myeon", "경기도 화성시 팔탄면 건달산로 219-63")]:
    res["s_" + name] = call(S, {"confmKey": SK, "currentPage": 1, "countPerPage": 3, "keyword": kw})
# 코드만으로 좌표: 동탄순환대로24길 101 (415974852381, BDong 106) / 진등3길 11-21 (415934431224, BDong 250) / 건달산로 219-63 (415913210004, BDong 340)
for name, adm, rn, mn, sn in [("dong_00", "4159710600", "415974852381", 101, 0), ("dong_old", "4159010600", "415974852381", 101, 0),
                              ("eup_00", "4159325000", "415934431224", 11, 21), ("myeon_00", "4159134000", "415913210004", 219, 63)]:
    res["c_" + name] = call(C, {"confmKey": CK, "admCd": adm, "rnMgtSn": rn, "udrtYn": 0, "buldMnnm": mn, "buldSlno": sn})
(OUT / "geo.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
print(json.dumps(res, ensure_ascii=False, indent=1))

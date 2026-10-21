"""좌표 변환 원천 도달 시험 (키는 출력하지 않음). 결과: probe/out/geo.json"""
import json, os, pathlib, urllib.parse, urllib.request, urllib.error

K = os.environ.get("VWORLD_KEY", "")
OUT = pathlib.Path("probe/out"); OUT.mkdir(parents=True, exist_ok=True)
res = {}

def hit(name, url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 next-step-care"})
        with urllib.request.urlopen(req, timeout=20) as r:
            t = f"{r.status} " + r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        t = f"HTTP {e.code} " + e.read().decode("utf-8", "replace")[:300]
    except Exception as e:
        t = f"EXC {type(e).__name__}: {e}"
    res[name] = (t.replace(K, "***") if K else t)[:600]

try:
    res["runner_ip"] = urllib.request.urlopen("https://ipinfo.io/json", timeout=10).read().decode()[:300]
except Exception as e:
    res["runner_ip"] = str(e)
a = urllib.parse.quote("경기도 화성시 동탄순환대로24길 101")
hit("vworld_http", f"http://api.vworld.kr/req/address?service=address&request=getcoord&version=2.0&crs=epsg:4326&type=road&format=json&key={K}&address={a}")
hit("vworld_https", f"https://api.vworld.kr/req/address?service=address&request=getcoord&version=2.0&crs=epsg:4326&type=road&format=json&key={K}&address={a}")
hit("juso_search_nokey", f"https://business.juso.go.kr/addrlink/addrLinkApi.do?confmKey=test&currentPage=1&countPerPage=1&resultType=json&keyword={a}")
hit("juso_coord_nokey", "https://business.juso.go.kr/addrlink/addrCoordApi.do?confmKey=test&resultType=json&admCd=4159710600&rnMgtSn=415974852381&udrtYn=0&buldMnnm=101&buldSlno=0")
hit("juso_home", "https://business.juso.go.kr/addrlink/main.do")
hit("kakao_nokey", f"https://dapi.kakao.com/v2/local/search/address.json?query={a}")
hit("naver_nokey", f"https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode?query={a}")
(OUT / "geo.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
print(json.dumps(res, ensure_ascii=False, indent=1))

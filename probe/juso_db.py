"""juso.go.kr 위치정보요약DB 다운로드 페이지 구조 조사. 결과: probe/out/juso_db/"""
import os, pathlib, re, urllib.request, http.cookiejar

OUT = pathlib.Path("probe/out/juso_db"); OUT.mkdir(parents=True, exist_ok=True)
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.addheaders = [("User-Agent", "Mozilla/5.0 next-step-care")]
pages = {
    "list_99MD": "https://business.juso.go.kr/addrlink/attrbDBDwld/attrbDBDwldList.do?cPath=99MD",
    "list_99MA": "https://business.juso.go.kr/addrlink/attrbDBDwld/attrbDBDwldList.do?cPath=99MA",
    "elctrnmap": "https://business.juso.go.kr/addrlink/elctrnMapProvd/geoDBDwldList.do",
    "dbmain": "https://business.juso.go.kr/addrlink/adresInfoProvd/guidance/provdAdresInfo.do",
}
for k, u in pages.items():
    try:
        t = op.open(u, timeout=30).read().decode("utf-8", "replace")
    except Exception as e:
        t = f"EXC {e}"
    (OUT / f"{k}.html").write_text(t)
    fns = sorted(set(re.findall(r"(fn_\w+|download\w*|\w+Dwld\w*)\s*\(", t)))[:60]
    links = sorted(set(re.findall(r"""(?:href|action)=["']([^"']*(?:dwld|Dwld|down|Down)[^"']*)""", t)))[:60]
    print("==", k, len(t), fns, links)

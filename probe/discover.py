"""공공데이터포털 탐색 5차: 숨은 조회 항목과 파일·표준데이터 내려받기 경로.

1) API 상세 페이지의 '오퍼레이션 선택'마다 포털 내부 조회(selectApiDetailFunction)로 요청주소·필드 표를 받는다
2) 장기요양기관 상세 9개 조회를 표본 기관 4곳으로 실제 호출
3) 파일·표준데이터 페이지에서 내려받기 경로와 API 주소를 찾고 앞부분을 받아 본다

결과는 probe/out/ 에 저장. 인증키는 어떤 출력에도 남기지 않는다.
"""
import html as H
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KEY = os.environ.get("DATA_GO_KR_KEY", "")
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (next-step-care probe)"}


def redact(s):
    if KEY:
        s = s.replace(KEY, "***").replace(urllib.parse.quote(KEY), "***")
    return re.sub(r"(serviceKey=)[^&\"' ]+", r"\1***", s)


def req(url, data=None, timeout=40, raw=False, limit=None):
    body = urllib.parse.urlencode(data).encode() if data else None
    r = urllib.request.Request(url, data=body, headers=UA)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            b = resp.read(limit) if limit else resp.read()
            return resp.status, (b if raw else b.decode("utf-8", "replace")), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), {}
    except Exception as e:  # noqa: BLE001
        return None, str(e), {}


def text(s):
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def api_ops(pk):
    st, page, _ = req(f"https://www.data.go.kr/data/{pk}/openapi.do")
    m = re.search(r'id="publicDataDetailPk" value="([^"]+)"', page)
    sel = re.search(r'id="open_api_detail_select".*?</select>', page, re.S)
    opts = re.findall(r'<option value="(\d+)">\s*([^<]+?)\s*</option>', sel.group(0)) if sel else []
    ops = []
    for seq, name in opts:
        st, frag, _ = req("https://www.data.go.kr/tcs/dss/selectApiDetailFunction.do",
                          {"oprtinSeqNo": seq, "publicDataDetailPk": m.group(1) if m else "", "publicDataPk": pk})
        t = text(frag)
        url = re.search(r"요청주소 (https?://\S+)", t)
        req_part = t.split("요청변수", 1)[-1].split("출력결과", 1)[0]
        res_part = t.split("출력결과", 1)[-1].split("오픈API 에러코드", 1)[0]
        ops.append({"seq": seq, "name": name, "url": url.group(1) if url else None,
                    "request": req_part[:1500], "response": res_part[:4000]})
    return {"pk": pk, "detailPk": m.group(1) if m else None, "ops": ops}


def call(url, params, limit=4000):
    q = urllib.parse.urlencode({"serviceKey": KEY, **params}, safe="")
    st, body, _ = req(f"{url}?{q}")
    return {"status": st, "body": redact(body[:limit])}


def file_page(pk, kind):
    st, page, _ = req(f"https://www.data.go.kr/data/{pk}/{kind}.do")
    info = {"pk": pk, "kind": kind, "status": st,
            "title": text(re.search(r"<title>(.*?)</title>", page, re.S).group(1)) if "<title>" in page else None}
    info["api_urls"] = sorted(set(re.findall(r"https?://api\.data\.go\.kr/openapi/[A-Za-z0-9_]+", page)))
    info["download_js"] = sorted(set(re.findall(r"(fn_fileDataDown|fileDetailSn|atchFileId|fn_fileDownload|downloadFile)[^;\n]{0,160}", page)))[:12]
    info["links"] = sorted(set(re.findall(r'href="(/[^"]*(?:download|Download|atchFile)[^"]*)"', page)))[:12]
    info["meta"] = {k: (re.search(rf'id="{k}" value="([^"]*)"', page) or [None, None])[1]
                    for k in ("publicDataPk", "publicDataDetailPk", "atchFileId", "fileDetailSn", "uddi")}
    (OUT / f"page_{pk}_{kind}.html").write_text(redact(page), encoding="utf-8")
    return info


def main():
    rep = {"key_set": bool(KEY)}

    rep["ops"] = [api_ops(pk) for pk in ("15058856", "15059029", "15158798", "15090532", "15108347")]

    samples = {"A03": "14159000629", "B03": "24159000520", "B01": "24159000614", "A04": "14159000013"}
    ltc_ops = next(x for x in rep["ops"] if x["pk"] == "15058856")["ops"]
    calls = {}
    for op in ltc_ops:
        if not op["url"]:
            continue
        for kind, sym in samples.items():
            calls[f'{op["name"]} | {kind}'] = call(op["url"], {"longTermAdminSym": sym, "adminPttnCd": kind, "pageNo": 1, "numOfRows": 20})
    rep["ltc_detail_calls"] = calls

    rep["files"] = [file_page(pk, k) for pk, k in (
        ("15104801", "fileData"),   # 장기요양기관 평가 결과
        ("15145962", "fileData"),   # 치매관련급여 제공 장기요양기관
        ("15155702", "standard"),   # 발달재활서비스 제공기관 표준
        ("15021138", "standard"),   # 치매센터 표준
        ("15028207", "standard"),   # 교통약자이동지원센터 표준
        ("15100058", "standard"),   # 장애인편의시설 표준
    )]

    (OUT / "report5.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    for x in rep["ops"]:
        print(x["pk"], [(o["name"], o["url"]) for o in x["ops"]])
    for k, v in calls.items():
        print(k, v["status"], v["body"][:150].replace("\n", " "))
    for f in rep["files"]:
        print(f["pk"], f["title"], f["api_urls"], f["meta"])


if __name__ == "__main__":
    main()

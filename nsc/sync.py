"""Cloudflare R2 동기화 — data/nsc ↔ 전용 버킷(next-step-care)

  python -m nsc sync pull      수집·가공에 필요한 것만 받음: state/ + 원천별 가장 최근 스냅샷
  python -m nsc sync pull-site Pages용: site/ 만 받음
  python -m nsc sync push      바뀐 파일만 올림 (지우지 않음)

환경변수: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
"""
import hashlib
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from .core import DATA, log


def client():
    need = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"]
    miss = [k for k in need if not os.environ.get(k)]
    if miss:
        sys.exit("R2 설정이 없습니다: " + ", ".join(miss))
    m = re.search(r"([0-9a-f]{32})", os.environ["R2_ACCOUNT_ID"])
    if not m:
        sys.exit("R2_ACCOUNT_ID 형식이 이상합니다 (32자리 16진수)")
    import boto3
    from botocore.config import Config
    s3 = boto3.client("s3", endpoint_url=f"https://{m.group(1)}.r2.cloudflarestorage.com",
                      aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                      aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                      region_name="auto",
                      config=Config(retries={"max_attempts": 5, "mode": "standard"}, max_pool_connections=16))
    return s3, os.environ["R2_BUCKET"]


def remote(s3, b):
    out, tok = {}, None
    while True:
        r = s3.list_objects_v2(Bucket=b, **({"ContinuationToken": tok} if tok else {}))
        for o in r.get("Contents", []):
            out[o["Key"]] = (o["Size"], o["ETag"].strip('"'))
        if not r.get("IsTruncated"):
            return out
        tok = r["NextContinuationToken"]


def _md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _same(p, sz, etag):
    return p.exists() and p.stat().st_size == sz and ("-" in etag or _md5(p) == etag)


def _latest_snaps(keys):
    best = {}
    for k in keys:
        m = re.match(r"snap/(\d{4}-\d{2}-\d{2})/([^/]+)$", k)
        if m and m.group(1) > best.get(m.group(2), ("",))[0]:
            best[m.group(2)] = (m.group(1), k)
    return {v[1] for v in best.values()}


def pull(site=False):
    s3, b = client()
    rem = remote(s3, b)
    want = {k for k in rem if k.startswith("site/")} if site else \
        ({k for k in rem if k.startswith("state/")} | _latest_snaps(rem))
    todo = [k for k in want if not _same(DATA / k, *rem[k])]

    def get(k):
        (DATA / k).parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(b, k, str(DATA / k))
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(get, todo))
    log(f"R2에서 받음 {len(todo)}개 (원격 {len(rem)}개)")


def push():
    s3, b = client()
    rem = remote(s3, b)
    files = [p for p in DATA.rglob("*") if p.is_file()]
    todo = [p for p in files if not (str(p.relative_to(DATA)) in rem and _same(p, *rem[str(p.relative_to(DATA))]))]

    def put(p):
        k = str(p.relative_to(DATA))
        ct = "application/json" if p.suffix == ".json" else "application/gzip" if p.suffix == ".gz" else "application/octet-stream"
        s3.upload_file(str(p), b, k, ExtraArgs={"ContentType": ct})
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(put, todo))
    log(f"R2에 올림 {len(todo)}개")

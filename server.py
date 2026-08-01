#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
포트폴리오 리밸런싱 도구 - 로컬 서버

이 스크립트를 실행하면:
1. 이 파일과 같은 폴더의 accounts/ 안에 계좌별 json 파일을 읽고 씁니다.
2. tool.html을 브라우저로 자동으로 엽니다.
3. 외부 네트워크에는 노출되지 않고, 127.0.0.1(이 PC 안)에서만 동작합니다.

종료하려면 이 창을 닫거나 Ctrl+C 를 누르세요.
"""

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

PORT = 8765
BASE_DIR = Path(__file__).resolve().parent
ACCOUNTS_DIR = BASE_DIR / "accounts"
INDEX_FILE = ACCOUNTS_DIR / "_index.json"
TOOL_HTML = BASE_DIR / "tool.html"
README_HTML = BASE_DIR / "README.html"

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
CODE_PATTERN = re.compile(r"^[0-9]{6}$")

NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://finance.naver.com/",
}
# 네이버 금융 종목 메인 페이지의 현재가/종목명 표시 영역에서 값을 추출하는 패턴입니다.
# 네이버가 페이지 구조를 바꾸면 이 패턴이 깨질 수 있습니다 - 그런 경우 직접 입력으로 대체해주세요.
NAVER_PRICE_RE = re.compile(r'class="today"[^>]*>.*?<span class="blind">([\d,]+)</span>', re.S)
NAVER_NAME_RE = re.compile(r'class="h_company"[^>]*>.*?<a[^>]*>([^<]+)</a>', re.S)


def fetch_naver_quote(code, max_retries=3, retry_delay=0.4):
    code = (code or "").strip()
    if not CODE_PATTERN.match(code):
        raise ValueError("종목코드는 숫자 6자리여야 합니다: %r" % code)

    url = "https://finance.naver.com/item/main.naver?code=%s" % code
    last_error = "알 수 없는 오류"

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=NAVER_HEADERS)
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()

            try:
                html = raw.decode("utf-8")
            except UnicodeDecodeError:
                html = raw.decode("euc-kr", errors="replace")

            price_match = NAVER_PRICE_RE.search(html)
            if not price_match:
                raise ValueError("현재가를 찾지 못했습니다 (코드 확인 또는 페이지 구조 변경 가능성).")
            price = int(price_match.group(1).replace(",", ""))

            name_match = NAVER_NAME_RE.search(html)
            name = name_match.group(1).strip() if name_match else None

            return {"code": code, "name": name, "price": price}
        except urllib.error.URLError as e:
            last_error = "네이버 금융에 접속할 수 없습니다: %s" % e
        except Exception as e:
            last_error = str(e)

        if attempt < max_retries - 1:
            time.sleep(retry_delay)  # 재시도 전 짧게 대기

    raise ValueError(last_error)


def account_path(account_id):
    return ACCOUNTS_DIR / (account_id + ".json")


def default_seed_account():
    return {
        "id": "acc-default",
        "name": "계좌 1",
        "cash": 848621,
        "additionalDeposit": 0,
        "etfs": [
            {"id": "etf-1", "name": "KIWOOM 국고채10년", "code": "148070", "avgPrice": 112435, "currentPrice": 117250, "qty": 86, "targetRatio": 10, "orderExecuted": False},
            {"id": "etf-2", "name": "PLUS 신흥국 MSCI(합성H)", "code": "195980", "avgPrice": 9795, "currentPrice": 11865, "qty": 2228, "targetRatio": 25, "orderExecuted": False},
            {"id": "etf-3", "name": "TIGER 단기채권액티브", "code": "272580", "avgPrice": 52293, "currentPrice": 56180, "qty": 181, "targetRatio": 10, "orderExecuted": False},
            {"id": "etf-4", "name": "TIGER 미국채10년선물", "code": "305080", "avgPrice": 11766, "currentPrice": 12715, "qty": 806, "targetRatio": 10, "orderExecuted": False},
            {"id": "etf-5", "name": "TIGER 미국S&P500", "code": "360750", "avgPrice": 15217, "currentPrice": 23200, "qty": 1137, "targetRatio": 25, "orderExecuted": False},
            {"id": "etf-6", "name": "ACE KRX금현물", "code": "411060", "avgPrice": 13568, "currentPrice": 24445, "qty": 952, "targetRatio": 20, "orderExecuted": False},
        ],
    }


def ensure_seed():
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    existing = [p for p in ACCOUNTS_DIR.glob("*.json") if p.name != "_index.json"]
    if existing:
        return
    seed = default_seed_account()
    account_path(seed["id"]).write_text(
        json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    INDEX_FILE.write_text(
        json.dumps({"order": [seed["id"]], "activeAccountId": seed["id"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_state():
    ensure_seed()
    try:
        index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        index = {"order": [], "activeAccountId": None}

    order = index.get("order") or []
    accounts = []
    valid_order = []
    for acc_id in order:
        if not ID_PATTERN.match(acc_id):
            continue
        path = account_path(acc_id)
        if not path.exists():
            continue
        try:
            accounts.append(json.loads(path.read_text(encoding="utf-8")))
            valid_order.append(acc_id)
        except Exception:
            continue

    # 인덱스에 없지만 폴더에 남아있는 파일도 복구 (수동으로 파일을 추가한 경우 등)
    known_ids = set(valid_order)
    for path in sorted(ACCOUNTS_DIR.glob("*.json")):
        if path.name == "_index.json":
            continue
        acc_id = path.stem
        if acc_id in known_ids or not ID_PATTERN.match(acc_id):
            continue
        try:
            accounts.append(json.loads(path.read_text(encoding="utf-8")))
            valid_order.append(acc_id)
        except Exception:
            continue

    if not accounts:
        ensure_seed()
        return read_state()

    active = index.get("activeAccountId")
    if active not in valid_order:
        active = valid_order[0]

    return {"accounts": accounts, "activeAccountId": active}


def write_state(payload):
    accounts = payload.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise ValueError("accounts가 비어있습니다.")

    keep_ids = []
    for acc in accounts:
        acc_id = acc.get("id")
        if not acc_id or not ID_PATTERN.match(acc_id):
            raise ValueError("잘못된 계좌 id 입니다: %r" % acc_id)
        keep_ids.append(acc_id)

    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)

    # 더 이상 존재하지 않는 계좌 파일 삭제 (탭에서 삭제된 계좌)
    for path in ACCOUNTS_DIR.glob("*.json"):
        if path.name == "_index.json":
            continue
        if path.stem not in keep_ids:
            try:
                path.unlink()
            except OSError:
                pass

    # 계좌별 파일에 그대로 덮어쓰기 (백업파일을 새로 만들지 않음)
    for acc in accounts:
        clean = {k: v for k, v in acc.items() if not k.startswith("_")}
        account_path(acc["id"]).write_text(
            json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    active = payload.get("activeAccountId")
    if active not in keep_ids:
        active = keep_ids[0]
    INDEX_FILE.write_text(
        json.dumps({"order": keep_ids, "activeAccountId": active}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        sys.stderr.write("[서버] " + (format % args) + "\n")

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path in ("/", "/tool.html"):
            if not TOOL_HTML.exists():
                self._send_html("<h1>tool.html을 찾을 수 없습니다.</h1>", status=500)
                return
            self._send_html(TOOL_HTML.read_text(encoding="utf-8"))
            return
        if parsed.path in ("/readme", "/README.html"):
            if not README_HTML.exists():
                self._send_html("<h1>README.html을 찾을 수 없습니다.</h1>", status=404)
                return
            self._send_html(README_HTML.read_text(encoding="utf-8"))
            return
        if parsed.path == "/api/state":
            try:
                self._send_json(read_state())
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        if self.path == "/api/state":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8")
                payload = json.loads(raw)
                write_state(payload)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            return
        if self.path == "/api/refresh-prices":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8")
                payload = json.loads(raw)
                codes = payload.get("codes") or []
                results = {}
                seen = set()
                for code in codes:
                    code = (code or "").strip()
                    if not code or code in seen:
                        continue
                    seen.add(code)
                    try:
                        results[code] = dict(ok=True, **fetch_naver_quote(code))
                    except Exception as e:
                        results[code] = {"ok": False, "error": str(e)}
                self._send_json({"ok": True, "results": results})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            return
        self._send_json({"error": "not found"}, status=404)


def main():
    ensure_seed()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print("이미 %d 포트에서 서버가 실행 중인 것 같습니다. 브라우저를 열어볼게요." % PORT)
        webbrowser.open("http://127.0.0.1:%d/tool.html" % PORT)
        return

    url = "http://127.0.0.1:%d/tool.html" % PORT
    print("포트폴리오 리밸런싱 도구 서버가 실행되었습니다: %s" % url)
    print("이 창을 닫거나 Ctrl+C 를 누르면 서버가 종료됩니다.")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("서버를 종료합니다.")


if __name__ == "__main__":
    main()

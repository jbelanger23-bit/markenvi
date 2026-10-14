# server.py
# The app. A local HTTP server with no framework: serves ui/index.html and a handful of
# JSON endpoints that call the funnel. Binds to 127.0.0.1 only; nothing is reachable
# from outside your machine.  python server.py  [--no-browser]
import json, os, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import settings
from config import HOST, PORT, LEADERS_FILE, LOG_FILE, DATA_DIR
import check as check_mod
import pick_traders
import paper

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "index.html")


def state() -> dict:
    log = check_mod.read_log()
    return {"settings": settings.public(), "leaders": pick_traders.load_leaders(),
            "log": list(reversed(log)), "data_dir": DATA_DIR}


ROUTES = {}


def route(method, path):
    def deco(fn):
        ROUTES[(method, path)] = fn
        return fn
    return deco


@route("GET", "/api/state")
def api_state(_):
    return state()


@route("POST", "/api/check")
def api_check(body):
    mint, handle = (body.get("mint") or "").strip(), (body.get("handle") or "").strip()
    if not mint or not handle:
        raise ValueError("mint and handle are required")
    return check_mod.check_and_log(mint, handle)


@route("POST", "/api/pick")
def api_pick(body):
    traders = body.get("traders")
    if not isinstance(traders, list) or not traders:
        raise ValueError("traders must be a non-empty list")
    for t in traders:
        missing = [k for k in pick_traders.FIELDS if k not in t]
        if missing:
            raise ValueError(f"{t.get('handle', '?')}: missing {', '.join(missing)}")
    out = pick_traders.pick(traders)
    if body.get("save", True) and out["leaders"]:
        pick_traders.save_leaders(out["leaders"])
    return out


@route("POST", "/api/leaders")
def api_leaders(body):
    leaders = body.get("leaders")
    if not isinstance(leaders, list):
        raise ValueError("leaders must be a list")
    pick_traders.save_leaders(leaders)
    return {"leaders": leaders}


@route("GET", "/api/paper")
def api_paper(_):
    return paper.mark()


@route("POST", "/api/paper/close")
def api_paper_close(body):
    return paper.close(str(body.get("id") or ""), body.get("reason") or "leader sold")


@route("POST", "/api/paper/reset")
def api_paper_reset(_):
    return paper.book(paper.reset(), settings.load())


@route("POST", "/api/settings")
def api_settings(body):
    return settings.public(settings.save(body))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):      # quiet unless it's an error
        if args and str(args[1]).startswith(("4", "5")):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(UI, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._dispatch("GET", path, {})

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "body must be JSON"})
        self._dispatch("POST", path, body)

    def _dispatch(self, method, path, body):
        fn = ROUTES.get((method, path))
        if not fn:
            return self._json(404, {"error": "not found"})
        try:
            self._json(200, fn(body))
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except Exception as e:                       # never a traceback page; the UI shows the text
            self._json(500, {"error": f"{type(e).__name__}: {e}"})


def serve(host=HOST, port=PORT, open_browser=True):
    os.makedirs(DATA_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"desk running at {url}   (ctrl+c to stop)")
    if open_browser:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    serve(open_browser="--no-browser" not in sys.argv)

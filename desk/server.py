# server.py
# The app. A local HTTP server with no framework: serves ui/index.html and a handful of
# JSON endpoints that call the funnel. Binds to 127.0.0.1 only; nothing is reachable
# from outside your machine.  python server.py  [--no-browser]
import base64, hmac, json, os, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import settings
from config import HOST, PORT, PASSWORD, PUBLIC, LEADERS_FILE, LOG_FILE, DATA_DIR
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


@route("GET", "/healthz")
def healthz(_):
    return {"ok": True}


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

    def _authed(self, path) -> bool:
        """HTTP Basic auth when DESK_PASSWORD is set. The health check stays open for the platform."""
        if not PASSWORD or path == "/healthz":
            return True
        hdr = self.headers.get("Authorization") or ""
        try:
            user, _, pw = base64.b64decode(hdr.split(" ", 1)[1]).decode().partition(":")
        except Exception:
            user, pw = "", ""
        if hdr.startswith("Basic ") and hmac.compare_digest(pw, PASSWORD):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="desk"')
        self.send_header("Content-Type", "application/json")
        body = b'{"error": "password required"}'
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._authed(path):
            return
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
        if not self._authed(path):
            return
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


def mark_once() -> list[str]:
    """One pass of the background marker. Skips the network when there is nothing open."""
    s = settings.load()
    if not s["paper"]["enabled"] or not paper.open_pos(paper.load(s=s)):
        return []
    events = paper.mark(s)["events"]
    for e in events:
        print(f"[paper] {time.strftime('%H:%M')} {e}")
    return events


def marker_loop(stop: threading.Event):
    """Runs while the server runs, so stops and take-profits fire with the browser closed."""
    while not stop.is_set():
        try:
            interval = float(settings.load()["paper"].get("mark_interval_sec") or 60)
        except Exception:
            interval = 60
        stop.wait(max(10, interval))
        if stop.is_set():
            break
        try:
            mark_once()
        except Exception as e:                      # never let one bad pass kill the loop
            print(f"[paper] mark failed: {e}", file=sys.stderr)


def serve(host=HOST, port=PORT, open_browser=True):
    if PUBLIC and not PASSWORD:
        raise SystemExit("refusing to listen on a public address without DESK_PASSWORD set")
    os.makedirs(DATA_DIR, exist_ok=True)
    stop = threading.Event()
    threading.Thread(target=marker_loop, args=(stop,), daemon=True, name="paper-marker").start()
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"desk running at {url}   (ctrl+c to stop)" + ("   [password on]" if PASSWORD else ""))
    if open_browser and not PUBLIC:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()


if __name__ == "__main__":
    serve(open_browser="--no-browser" not in sys.argv)

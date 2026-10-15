# Endpoint tests against a live server on a random port, with the funnel's network calls mocked.
import json, os, sys, tempfile, threading, unittest, urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["DESK_DATA_DIR"] = cls.tmp.name
        for m in list(sys.modules):
            if m in ("config", "settings", "check", "pick_traders", "rugcheck", "judge", "server"):
                del sys.modules[m]
        import server
        cls.server = server
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.tmp.cleanup()
        os.environ.pop("DESK_DATA_DIR", None)

    def call(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_index_served(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as r:
            html = r.read().decode()
        self.assertIn("<title>desk</title>", html)

    def test_full_flow(self):
        code, st = self.call("/api/state")
        self.assertEqual(code, 200); self.assertEqual(st["leaders"], []); self.assertIn("key_set", st["settings"])

        code, _ = self.call("/api/check", {"mint": "M", "handle": ""})
        self.assertEqual(code, 400)

        code, s = self.call("/api/settings", {"anthropic_api_key": "sk-ant-x", "token": {"max_risk_score": 35}})
        self.assertEqual(code, 200); self.assertTrue(s["key_set"]); self.assertEqual(s["token"]["max_risk_score"], 35)
        self.assertNotIn("anthropic_api_key", s)

        trader = {"handle": "alice", "pnl_30d_usd": 1000, "pnl_7d_usd": 100, "closed_trades": 40,
                  "win_rate": 0.6, "top_coin_share": 0.2, "avg_hold_minutes": 120, "followers": 50}
        code, err = self.call("/api/pick", {"traders": [{"handle": "x"}]})
        self.assertEqual(code, 400); self.assertIn("missing", err["error"])

        with mock.patch.object(self.server.pick_traders, "ask",
                               return_value={"leaders": [{"handle": "alice", "rank": 1, "why": "w", "risk": "r"}], "rejected": []}):
            code, out = self.call("/api/pick", {"traders": [trader]})
        self.assertEqual(code, 200); self.assertEqual(out["leaders"][0]["handle"], "alice")

        token = {"mint": "M", "symbol": "OK", "risk_score": 40, "danger": [], "warn": [], "lp_locked_pct": 100,
                 "top10_pct": 12.0, "insider_nets": 0, "mint_authority": None, "freeze_authority": None}
        with mock.patch.object(self.server.check_mod, "report", return_value=token):
            code, v = self.call("/api/check", {"mint": "M", "handle": "alice"})
        self.assertEqual(code, 200)
        self.assertEqual((v["verdict"], v["reason"], v["stage"]), ("SKIP", "risk_score", "facts"))  # the 35 override bit

        code, st = self.call("/api/state")
        self.assertEqual(len(st["log"]), 1); self.assertEqual(st["log"][0]["reason"], "risk_score")

        code, out = self.call("/api/leaders", {"leaders": []})
        self.assertEqual(code, 200); self.assertEqual(self.call("/api/state")[1]["leaders"], [])

    def test_paper_endpoints_and_boolean_settings(self):
        code, s = self.call("/api/settings", {"paper": {"enabled": "false", "max_positions": "3"}})
        self.assertEqual(code, 200); self.assertIs(s["paper"]["enabled"], False); self.assertEqual(s["paper"]["max_positions"], 3)
        self.call("/api/settings", {"paper": {"enabled": "true"}})
        with mock.patch.object(self.server.paper, "price_usd", return_value=2.0):
            self.server.paper.try_open("MINT", "SYM", "alice")
            code, b = self.call("/api/paper")
        self.assertEqual(code, 200); self.assertEqual(len(b["open"]), 1); self.assertAlmostEqual(b["equity"], 320)
        with mock.patch.object(self.server.paper, "price_usd", return_value=3.0):
            code, b = self.call("/api/paper/close", {"id": b["open"][0]["id"]})
        self.assertEqual(code, 200); self.assertAlmostEqual(b["realized"], 25); self.assertEqual(b["open"], [])
        self.assertEqual(self.call("/api/paper/close", {"id": "nope"})[0], 400)
        code, b = self.call("/api/paper/reset", {})
        self.assertEqual(code, 200); self.assertEqual(b["closed"], []); self.assertAlmostEqual(b["cash"], 320)

    def test_background_marker_pass(self):
        self.call("/api/paper/reset", {})
        self.assertEqual(self.server.mark_once(), [])          # nothing open: no network, no events
        with mock.patch.object(self.server.paper, "price_usd", return_value=1.0):
            self.server.paper.try_open("MINT2", "SYM", "alice")
        with mock.patch.object(self.server.paper, "price_usd", return_value=0.3):
            events = self.server.mark_once()
        self.assertEqual(len(events), 1); self.assertIn("stop loss", events[0])
        self.assertEqual(self.call("/api/paper")[1]["open"], [])
        self.call("/api/paper/reset", {})

    def test_password_gate_when_set(self):
        import base64
        with mock.patch.object(self.server, "PASSWORD", "hunter2"):
            self.assertEqual(self.call("/api/state")[0], 401)
            self.assertEqual(self.call("/api/check", {"mint": "M", "handle": "x"})[0], 401)
            self.assertEqual(self.call("/healthz")[0], 200)          # platform health check stays open
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/state",
                                         headers={"Authorization": "Basic " + base64.b64encode(b"desk:hunter2").decode()})
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, 200)
            bad = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/state",
                                         headers={"Authorization": "Basic " + base64.b64encode(b"desk:nope").decode()})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(bad)
            self.assertEqual(cm.exception.code, 401)

    def test_public_bind_without_password_refuses_to_start(self):
        with mock.patch.object(self.server, "PUBLIC", True), mock.patch.object(self.server, "PASSWORD", ""):
            with self.assertRaises(SystemExit):
                self.server.serve(open_browser=False)

    def test_unknown_route_is_json_404(self):
        self.assertEqual(self.call("/api/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()

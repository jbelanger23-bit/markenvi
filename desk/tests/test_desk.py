# Run from the desk folder:  python -m unittest discover -s tests
# No network. Rugcheck and xAI are mocked; the funnel rules are what gets tested.
import json, os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import check as check_mod
import grok
import pick_traders
import rugcheck
from config import MIN_CONFIDENCE, TOKEN, TRADER


def clean_token(**over):
    t = {"mint": "M", "symbol": "OK", "risk_score": 10, "danger": [], "warn": [],
         "lp_locked_pct": 100, "top10_pct": 12.0, "insider_nets": 0,
         "mint_authority": None, "freeze_authority": None}
    t.update(over)
    return t


def good_trader(**over):
    t = {"handle": "alice", "pnl_30d_usd": 1000, "pnl_7d_usd": 100, "closed_trades": 40,
         "win_rate": 0.6, "top_coin_share": 0.2, "avg_hold_minutes": 120, "followers": 50}
    t.update(over)
    return t


class FactKill(unittest.TestCase):
    def test_clean_token_passes(self):
        self.assertIsNone(rugcheck.fact_kill(clean_token()))

    def test_first_failure_wins_in_order(self):
        cases = [
            ({"risk_score": None}, "no_report"),
            ({"mint_authority": "abc"}, "authority_open"),
            ({"freeze_authority": "abc"}, "authority_open"),
            ({"danger": ["Mutable metadata"]}, "danger:Mutable metadata"),
            ({"risk_score": TOKEN["max_risk_score"] + 1}, "risk_score"),
            ({"top10_pct": TOKEN["max_top10_pct"] + 0.1}, "top10"),
            ({"lp_locked_pct": TOKEN["min_lp_locked_pct"] - 1}, "lp_unlocked"),
            ({"insider_nets": TOKEN["max_insider_nets"] + 1}, "insiders"),
        ]
        for over, want in cases:
            with self.subTest(over=over):
                self.assertEqual(rugcheck.fact_kill(clean_token(**over)), want)

    def test_null_fields_are_not_a_pass_but_not_a_kill_either(self):
        # Missing top10 / lp fields stay null for Grok to see; code only kills on no score.
        self.assertIsNone(rugcheck.fact_kill(clean_token(top10_pct=None, lp_locked_pct=None)))

    def test_thresholds_are_inclusive(self):
        self.assertIsNone(rugcheck.fact_kill(clean_token(
            risk_score=TOKEN["max_risk_score"], top10_pct=TOKEN["max_top10_pct"],
            lp_locked_pct=TOKEN["min_lp_locked_pct"])))


class Report(unittest.TestCase):
    @mock.patch("rugcheck.requests.get")
    def test_trims_raw_report(self, get):
        get.return_value.json.return_value = {
            "tokenMeta": {"symbol": "ABC"}, "score_normalised": 23,
            "risks": [{"name": "Low LP", "level": "warn"}, {"name": "Honeypot", "level": "danger"}],
            "lpLockedPct": 95.5,
            "topHolders": [{"address": str(i), "pct": 2.0} for i in range(15)],
            "insiderNetworks": [{"id": 1}],
            "token": {"mintAuthority": None, "freezeAuthority": "F"},
        }
        t = rugcheck.report("MINT")
        self.assertEqual(t["symbol"], "ABC")
        self.assertEqual(t["risk_score"], 23)
        self.assertEqual(t["danger"], ["Honeypot"])
        self.assertEqual(t["warn"], ["Low LP"])
        self.assertEqual(t["top10_pct"], 20.0)   # only the first 10 holders
        self.assertEqual(t["insider_nets"], 1)
        self.assertEqual(t["freeze_authority"], "F")
        get.assert_called_once_with(f"{rugcheck.RC}/MINT/report", timeout=20)

    @mock.patch("rugcheck.requests.get")
    def test_missing_fields_stay_null(self, get):
        get.return_value.json.return_value = {}
        t = rugcheck.report("MINT")
        self.assertIsNone(t["risk_score"])
        self.assertIsNone(t["top10_pct"])
        self.assertIsNone(t["lp_locked_pct"])
        self.assertEqual(t["insider_nets"], 0)


class Grok(unittest.TestCase):
    @mock.patch.dict(os.environ, {"XAI_API_KEY": "xai-test"})
    @mock.patch("grok.requests.post")
    def test_json_extracted_from_prose_wrapper(self, post):
        post.return_value.json.return_value = {"choices": [{"message": {
            "content": 'Sure.\n{"verdict": "COPY", "confidence": 0.8}\nThanks.'}}]}
        self.assertEqual(grok.ask("sys", {"a": 1}), {"verdict": "COPY", "confidence": 0.8})
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["messages"][1]["content"], json.dumps({"a": 1}))
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer xai-test")

    @mock.patch.dict(os.environ, {"XAI_API_KEY": "xai-test"})
    @mock.patch("grok.requests.post")
    def test_prose_only_raises(self, post):
        post.return_value.json.return_value = {"choices": [{"message": {"content": "I think copy it"}}]}
        with self.assertRaises(ValueError):
            grok.ask("sys", {})

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_key_raises_without_network(self):
        with mock.patch("grok.requests.post") as post:
            with self.assertRaises(RuntimeError):
                grok.ask("sys", {})
            post.assert_not_called()


class CodeCut(unittest.TestCase):
    def test_good_trader_passes(self):
        self.assertIsNone(pick_traders.code_cut(good_trader()))

    def test_cuts(self):
        cases = [
            ({"closed_trades": TRADER["min_closed_trades"] - 1}, "too_few_trades"),
            ({"win_rate": TRADER["min_win_rate"] - 0.01}, "win_rate"),
            ({"top_coin_share": TRADER["max_top_coin_share"] + 0.01}, "one_coin_luck"),
            ({"pnl_7d_usd": TRADER["min_pnl_7d_usd"] - 1}, "red_week"),
        ]
        for over, want in cases:
            with self.subTest(over=over):
                self.assertEqual(pick_traders.code_cut(good_trader(**over)), want)

    @mock.patch("pick_traders.ask")
    def test_pick_drops_invented_handles_and_merges_stats(self, ask):
        ask.return_value = {"leaders": [
            {"handle": "alice", "rank": 1, "why": "w", "risk": "r"},
            {"handle": "ghost", "rank": 2, "why": "w", "risk": "r"}]}
        leaders = pick_traders.pick([good_trader(), good_trader(handle="bob", win_rate=0.1)])
        self.assertEqual([l["handle"] for l in leaders], ["alice"])
        self.assertEqual(leaders[0]["closed_trades"], 40)
        self.assertEqual(leaders[0]["rank"], 1)
        # only the traders that passed the cut reach Grok
        self.assertEqual([t["handle"] for t in ask.call_args.args[1]["traders"]], ["alice"])

    @mock.patch("pick_traders.ask")
    def test_nobody_passes_never_calls_grok(self, ask):
        with self.assertRaises(SystemExit):
            pick_traders.pick([good_trader(win_rate=0.1)])
        ask.assert_not_called()


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.leaders = os.path.join(self.tmp.name, "leaders.json")
        with open(self.leaders, "w") as f:
            json.dump([good_trader()], f)

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, handle="alice", token=None, ans=None, report_exc=None, ask_exc=None):
        with mock.patch("check.report") as rep, mock.patch("check.ask") as ask:
            if report_exc: rep.side_effect = report_exc
            else: rep.return_value = token or clean_token()
            if ask_exc: ask.side_effect = ask_exc
            else: ask.return_value = ans or {"verdict": "COPY", "confidence": 0.9, "why": "fine", "main_risk": "x"}
            v = check_mod.check("MINT", handle, leaders_file=self.leaders)
            return v, rep, ask

    def test_not_a_leader_skips_before_any_network(self):
        v, rep, ask = self.run_check(handle="stranger")
        self.assertEqual(v, {"verdict": "SKIP", "reason": "not a leader"})
        rep.assert_not_called(); ask.assert_not_called()

    def test_missing_leaders_file_skips(self):
        v = check_mod.check("MINT", "alice", leaders_file=os.path.join(self.tmp.name, "nope.json"))
        self.assertEqual(v["verdict"], "SKIP")
        self.assertIn("no leaders file", v["reason"])

    def test_rugcheck_failure_skips(self):
        v, _, ask = self.run_check(report_exc=TimeoutError("timeout"))
        self.assertEqual(v["verdict"], "SKIP")
        self.assertIn("rugcheck failed", v["reason"])
        ask.assert_not_called()

    def test_fact_kill_skips_before_grok(self):
        v, _, ask = self.run_check(token=clean_token(mint_authority="X"))
        self.assertEqual(v, {"verdict": "SKIP", "reason": "authority_open", "symbol": "OK"})
        ask.assert_not_called()

    def test_grok_failure_skips(self):
        v, _, _ = self.run_check(ask_exc=ValueError("no json in answer"))
        self.assertEqual(v["verdict"], "SKIP")
        self.assertIn("grok failed", v["reason"])

    def test_copy_passes_through_with_symbol_and_reason(self):
        v, _, ask = self.run_check()
        self.assertEqual(v["verdict"], "COPY")
        self.assertEqual(v["symbol"], "OK")
        self.assertEqual(v["reason"], "fine")
        payload = ask.call_args.args[1]
        self.assertEqual(set(payload), {"leader", "token"})
        self.assertEqual(payload["leader"]["handle"], "alice")

    def test_low_confidence_copy_becomes_skip(self):
        v, _, _ = self.run_check(ans={"verdict": "COPY", "confidence": MIN_CONFIDENCE - 0.01, "why": "w"})
        self.assertEqual(v["verdict"], "SKIP")
        self.assertIn("low confidence", v["reason"])

    def test_confidence_at_threshold_is_copy(self):
        v, _, _ = self.run_check(ans={"verdict": "COPY", "confidence": MIN_CONFIDENCE, "why": "w"})
        self.assertEqual(v["verdict"], "COPY")

    def test_copy_without_confidence_becomes_skip(self):
        v, _, _ = self.run_check(ans={"verdict": "COPY", "why": "w"})
        self.assertEqual(v["verdict"], "SKIP")

    def test_unknown_verdict_becomes_skip(self):
        v, _, _ = self.run_check(ans={"verdict": "MAYBE", "confidence": 0.99, "why": "w"})
        self.assertEqual(v["verdict"], "SKIP")

    def test_log_writes_header_once(self):
        path = os.path.join(self.tmp.name, "checks.csv")
        row = {"time": "t", "leader": "alice", "mint": "M", "symbol": "OK",
               "verdict": "SKIP", "confidence": None, "reason": "top10"}
        check_mod.log(row, path); check_mod.log(row, path)
        with open(path) as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(check_mod.FIELDS))
        self.assertEqual(len(lines), 3)


if __name__ == "__main__":
    unittest.main()

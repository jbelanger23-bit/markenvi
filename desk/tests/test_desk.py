# Run from the desk folder:  python -m unittest discover -s tests
# No network. Rugcheck and the Anthropic API are mocked; the funnel rules are what gets tested.
import json, os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic
import check as check_mod
import judge
import pick_traders
import rugcheck
import settings
from config import TOKEN, TRADER, MIN_CONFIDENCE

DEFAULTS = settings.load("/nonexistent/settings.json")   # pure config.py defaults


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
    def kill(self, **over):
        return rugcheck.fact_kill(clean_token(**over), TOKEN)

    def test_clean_token_passes(self):
        self.assertIsNone(self.kill())

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
                self.assertEqual(self.kill(**over), want)

    def test_null_fields_are_not_a_pass_but_not_a_kill_either(self):
        self.assertIsNone(self.kill(top10_pct=None, lp_locked_pct=None))

    def test_thresholds_are_inclusive(self):
        self.assertIsNone(self.kill(risk_score=TOKEN["max_risk_score"], top10_pct=TOKEN["max_top10_pct"],
                                    lp_locked_pct=TOKEN["min_lp_locked_pct"]))

    def test_settings_override_applies(self):
        self.assertEqual(rugcheck.fact_kill(clean_token(risk_score=20), {**TOKEN, "max_risk_score": 10}), "risk_score")


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
        self.assertIsNone(t["risk_score"]); self.assertIsNone(t["top10_pct"]); self.assertIsNone(t["lp_locked_pct"])
        self.assertEqual(t["insider_nets"], 0)


def fake_response(text, stop="end_turn"):
    r = mock.Mock()
    r.stop_reason = stop
    r.stop_details = None
    blk = mock.Mock(); blk.type = "text"; blk.text = text
    r.content = [blk]
    return r


class Judge(unittest.TestCase):
    S = {**DEFAULTS, "anthropic_api_key": "sk-ant-test"}

    @mock.patch("judge.anthropic.Anthropic")
    def test_one_call_with_schema_and_fallbacks(self, A):
        A.return_value.beta.messages.create.return_value = fake_response('{"verdict": "COPY", "confidence": 0.8}')
        out = judge.ask("sys", {"b": 1, "a": 2}, {"type": "object"}, self.S)
        self.assertEqual(out, {"verdict": "COPY", "confidence": 0.8})
        A.assert_called_once(); self.assertEqual(A.call_args.kwargs["api_key"], "sk-ant-test")
        kw = A.return_value.beta.messages.create.call_args.kwargs
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["system"], "sys")
        self.assertEqual(kw["messages"], [{"role": "user", "content": '{"a": 2, "b": 1}'}])
        self.assertEqual(kw["output_config"]["format"], {"type": "json_schema", "schema": {"type": "object"}})
        self.assertEqual(kw["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", kw["betas"])

    @mock.patch("judge.anthropic.Anthropic")
    def test_missing_key_raises_without_network(self, A):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(judge.JudgeError):
                judge.ask("sys", {}, {}, {**DEFAULTS, "anthropic_api_key": ""})
        A.assert_not_called()

    @mock.patch("judge.anthropic.Anthropic")
    def test_refusal_and_early_stop_raise(self, A):
        for stop in ("refusal", "max_tokens"):
            A.return_value.beta.messages.create.return_value = fake_response("{}", stop)
            with self.assertRaises(judge.JudgeError):
                judge.ask("sys", {}, {}, self.S)

    @mock.patch("judge.anthropic.Anthropic")
    def test_api_errors_become_judge_errors(self, A):
        resp = mock.Mock(status_code=429, headers={})
        A.return_value.beta.messages.create.side_effect = anthropic.RateLimitError("rl", response=resp, body=None)
        with self.assertRaises(judge.JudgeError) as cm:
            judge.ask("sys", {}, {}, self.S)
        self.assertIn("429", str(cm.exception))

    @mock.patch("judge.anthropic.Anthropic")
    def test_non_json_raises(self, A):
        A.return_value.beta.messages.create.return_value = fake_response("I think copy it")
        with self.assertRaises(judge.JudgeError):
            judge.ask("sys", {}, {}, self.S)


class CodeCut(unittest.TestCase):
    def test_good_trader_passes(self):
        self.assertIsNone(pick_traders.code_cut(good_trader(), TRADER))

    def test_cuts(self):
        cases = [
            ({"closed_trades": TRADER["min_closed_trades"] - 1}, "too_few_trades"),
            ({"win_rate": TRADER["min_win_rate"] - 0.01}, "win_rate"),
            ({"top_coin_share": TRADER["max_top_coin_share"] + 0.01}, "one_coin_luck"),
            ({"pnl_7d_usd": TRADER["min_pnl_7d_usd"] - 1}, "red_week"),
        ]
        for over, want in cases:
            with self.subTest(over=over):
                self.assertEqual(pick_traders.code_cut(good_trader(**over), TRADER), want)

    @mock.patch("pick_traders.ask")
    def test_pick_drops_invented_handles_and_merges_stats(self, ask):
        ask.return_value = {"leaders": [
            {"handle": "ghost", "rank": 2, "why": "w", "risk": "r"},
            {"handle": "alice", "rank": 1, "why": "w", "risk": "r"}], "rejected": []}
        out = pick_traders.pick([good_trader(), good_trader(handle="bob", win_rate=0.1)], DEFAULTS)
        self.assertEqual([l["handle"] for l in out["leaders"]], ["alice"])
        self.assertEqual(out["leaders"][0]["closed_trades"], 40)
        self.assertEqual(out["cut"], [{"handle": "bob", "reason": "win_rate"}])
        self.assertEqual([t["handle"] for t in ask.call_args.args[1]["traders"]], ["alice"])

    @mock.patch("pick_traders.ask")
    def test_nobody_passes_never_calls_judge(self, ask):
        out = pick_traders.pick([good_trader(win_rate=0.1)], DEFAULTS)
        self.assertEqual(out["leaders"], []); self.assertIn("nobody passed", out["note"])
        ask.assert_not_called()


class Settings(unittest.TestCase):
    def test_save_merges_and_coerces(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            s = settings.save({"token": {"max_risk_score": "40", "bogus": 1}, "min_confidence": "0.7", "junk": 1}, p)
            self.assertEqual(s["token"]["max_risk_score"], 40)
            self.assertEqual(s["token"]["max_top10_pct"], TOKEN["max_top10_pct"])   # untouched default
            self.assertEqual(s["min_confidence"], 0.7)
            self.assertNotIn("junk", s); self.assertNotIn("bogus", s["token"])
            s = settings.save({"anthropic_api_key": "sk-x"}, p)
            self.assertEqual(s["token"]["max_risk_score"], 40)                       # earlier override kept
            pub = settings.public(s)
            self.assertTrue(pub["key_set"]); self.assertNotIn("anthropic_api_key", pub)

    def test_missing_file_gives_defaults(self):
        self.assertEqual(settings.load("/nonexistent/x.json")["model"], "claude-opus-5-5")


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.leaders = os.path.join(self.tmp.name, "leaders.json")
        pick_traders.save_leaders([{**good_trader(), "rank": 1, "why": "w", "risk": "r"}], self.leaders)

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, handle="alice", token=None, ans=None, report_exc=None, ask_exc=None):
        with mock.patch("check.report") as rep, mock.patch("check.ask") as ask:
            if report_exc: rep.side_effect = report_exc
            else: rep.return_value = token or clean_token()
            if ask_exc: ask.side_effect = ask_exc
            else: ask.return_value = ans or {"verdict": "COPY", "confidence": 0.9, "why": "fine", "main_risk": "x"}
            v = check_mod.check("MINT", handle, leaders_file=self.leaders, s=DEFAULTS)
            return v, rep, ask

    def test_not_a_leader_skips_before_any_network(self):
        v, rep, ask = self.run_check(handle="stranger")
        self.assertEqual(v, {"verdict": "SKIP", "reason": "not a leader", "stage": "leader"})
        rep.assert_not_called(); ask.assert_not_called()

    def test_rugcheck_failure_skips(self):
        v, _, ask = self.run_check(report_exc=TimeoutError("timeout"))
        self.assertEqual((v["verdict"], v["stage"]), ("SKIP", "rugcheck")); ask.assert_not_called()

    def test_fact_kill_skips_before_judge(self):
        v, _, ask = self.run_check(token=clean_token(mint_authority="X"))
        self.assertEqual((v["verdict"], v["reason"], v["stage"]), ("SKIP", "authority_open", "facts"))
        ask.assert_not_called()

    def test_judge_failure_skips(self):
        v, _, _ = self.run_check(ask_exc=judge.JudgeError("no json in answer"))
        self.assertEqual((v["verdict"], v["stage"]), ("SKIP", "judge")); self.assertIn("judge failed", v["reason"])

    def test_copy_passes_through_and_judge_never_sees_rank_notes(self):
        v, _, ask = self.run_check()
        self.assertEqual((v["verdict"], v["symbol"], v["reason"], v["stage"]), ("COPY", "OK", "fine", "done"))
        payload = ask.call_args.args[1]
        self.assertEqual(set(payload), {"leader", "token"})
        self.assertNotIn("why", payload["leader"]); self.assertEqual(payload["leader"]["handle"], "alice")

    def test_low_confidence_copy_becomes_skip(self):
        v, _, _ = self.run_check(ans={"verdict": "COPY", "confidence": MIN_CONFIDENCE - 0.01, "why": "w"})
        self.assertEqual((v["verdict"], v["stage"]), ("SKIP", "confidence")); self.assertIn("low confidence", v["reason"])

    def test_confidence_at_threshold_is_copy(self):
        v, _, _ = self.run_check(ans={"verdict": "COPY", "confidence": MIN_CONFIDENCE, "why": "w"})
        self.assertEqual(v["verdict"], "COPY")

    def test_unknown_verdict_becomes_skip(self):
        v, _, _ = self.run_check(ans={"verdict": "MAYBE", "confidence": 0.99, "why": "w"})
        self.assertEqual(v["verdict"], "SKIP")

    def test_log_round_trips(self):
        path = os.path.join(self.tmp.name, "checks.csv")
        with mock.patch("check.report", return_value=clean_token(mint_authority="X")):
            check_mod.check_and_log("MINT", "alice", leaders_file=self.leaders, log_file=path, s=DEFAULTS)
            check_mod.check_and_log("MINT", "nobody", leaders_file=self.leaders, log_file=path, s=DEFAULTS)
        rows = check_mod.read_log(path)
        self.assertEqual([r["reason"] for r in rows], ["authority_open", "not a leader"])
        self.assertEqual(rows[0]["stage"], "facts")


if __name__ == "__main__":
    unittest.main()

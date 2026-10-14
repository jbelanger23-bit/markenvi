import os, sys, tempfile, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paper, prices, settings

S = settings.load("/nonexistent/settings.json")   # defaults: $320 start, $50 ticket, 5 max, 24h, -50 / +100 half


class Paper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "paper.json")

    def tearDown(self):
        self.tmp.cleanup()

    def open(self, mint="A", price=1.0, leader="alice"):
        return paper.try_open(mint, mint, leader, S, self.path, price=price)

    def test_fill_debits_cash_and_sizes_by_ticket(self):
        r = self.open(price=0.5)
        self.assertTrue(r["filled"]); self.assertAlmostEqual(r["position"]["qty"], 100)
        b = paper.book(paper.load(self.path, S), S)
        self.assertAlmostEqual(b["cash"], 270); self.assertAlmostEqual(b["equity"], 320)

    def test_guards(self):
        self.open("A")
        self.assertIn("first buy wins", self.open("A")["reason"])
        for m in "BCDE": self.assertTrue(self.open(m)["filled"])
        self.assertIn("max positions", self.open("F")["reason"])

    def test_no_price_no_fill(self):
        with mock.patch("paper.price_usd", return_value=None):
            self.assertIn("no price", paper.try_open("A", "A", "alice", S, self.path)["reason"])
        with mock.patch("paper.price_usd", side_effect=TimeoutError("t")):
            self.assertIn("price failed", paper.try_open("A", "A", "alice", S, self.path)["reason"])
        self.assertEqual(paper.load(self.path, S)["positions"], [])

    def test_stop_loss_closes_whole_position(self):
        self.open("A", price=1.0)
        b = paper.mark(S, self.path, prices={"A": 0.5})
        self.assertEqual(b["open"], []); self.assertEqual(b["closed"][0]["exit_reason"], "stop loss -50%")
        self.assertAlmostEqual(b["cash"], 295); self.assertAlmostEqual(b["realized"], -25); self.assertEqual(b["losses"], 1)

    def test_take_profit_sells_half_once_then_rides(self):
        self.open("A", price=1.0)
        b = paper.mark(S, self.path, prices={"A": 2.0})
        p = b["open"][0]
        self.assertTrue(p["tp_taken"]); self.assertAlmostEqual(p["qty"], 25); self.assertAlmostEqual(b["cash"], 320)
        self.assertAlmostEqual(p["realized"], 25)
        b = paper.mark(S, self.path, prices={"A": 4.0})          # no second take
        self.assertAlmostEqual(b["open"][0]["qty"], 25); self.assertAlmostEqual(b["equity"], 420)
        b = paper.mark(S, self.path, prices={"A": 0.4})          # the rest still has its stop
        self.assertEqual(b["open"], []); self.assertAlmostEqual(b["realized"], 25 + (0.4 - 1) * 25)

    def test_price_error_keeps_position_and_last_price(self):
        self.open("A", price=1.0)
        with mock.patch("paper.price_usd", side_effect=TimeoutError("down")):
            b = paper.mark(S, self.path)
        self.assertEqual(b["open"][0]["price_error"], "down"); self.assertEqual(b["open"][0]["last"], 1.0)

    def test_leader_sold_closes_and_blocks_rebuy(self):
        pid = self.open("A", price=1.0)["position"]["id"]
        b = paper.close(pid, s=S, path=self.path, price=1.5)
        self.assertEqual(b["closed"][0]["exit_reason"], "leader sold"); self.assertAlmostEqual(b["realized"], 25)
        self.assertIn("no re-buy", self.open("A")["reason"])
        later = time.time() + 25 * 3600
        self.assertIsNone(paper.guard(paper.load(self.path, S), "A", S, at=later))
        with self.assertRaises(ValueError):
            paper.close(pid, s=S, path=self.path, price=1.0)

    def test_reset(self):
        self.open("A"); paper.reset(self.path, S)
        self.assertEqual(paper.load(self.path, S), {"cash": 320.0, "positions": []})


class Prices(unittest.TestCase):
    @mock.patch("prices.requests.get")
    def test_most_liquid_solana_pair_wins(self, get):
        get.return_value.json.return_value = {"pairs": [
            {"chainId": "solana", "priceUsd": "1.0", "liquidity": {"usd": 100}},
            {"chainId": "solana", "priceUsd": "1.2", "liquidity": {"usd": 5000}},
            {"chainId": "base", "priceUsd": "9", "liquidity": {"usd": 99999}}]}
        self.assertEqual(prices.price_usd("M"), 1.2)

    @mock.patch("prices.requests.get")
    def test_no_pairs_is_none(self, get):
        get.return_value.json.return_value = {"pairs": None}
        self.assertIsNone(prices.price_usd("M"))


class CheckWiring(unittest.TestCase):
    def test_copy_opens_paper_position(self):
        import check as check_mod, pick_traders
        with tempfile.TemporaryDirectory() as d:
            lf, pf, log = (os.path.join(d, n) for n in ("l.json", "p.json", "c.csv"))
            pick_traders.save_leaders([{"handle": "alice"}], lf)
            tok = {"mint": "M", "symbol": "OK", "risk_score": 1, "danger": [], "warn": [], "lp_locked_pct": 100,
                   "top10_pct": 5, "insider_nets": 0, "mint_authority": None, "freeze_authority": None}
            with mock.patch("check.report", return_value=tok), \
                 mock.patch("check.ask", return_value={"verdict": "COPY", "confidence": 0.9, "why": "w", "main_risk": "r"}):
                v = check_mod.check_and_log("M", "alice", lf, log, S, path=pf, price=2.0)
            self.assertTrue(v["paper"]["filled"]); self.assertAlmostEqual(v["paper"]["position"]["qty"], 25)
            self.assertEqual(len(paper.load(pf, S)["positions"]), 1)


if __name__ == "__main__":
    unittest.main()

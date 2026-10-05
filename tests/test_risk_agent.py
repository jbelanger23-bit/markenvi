from datetime import datetime, timezone, timedelta

from hlagent.config import RiskConfig, Settings, StrategyConfig
from hlagent.risk import RiskEngine
from hlagent.signals import Signal
from hlagent.execution import PaperBroker
from hlagent.agent import Agent
from hlagent.learning import Learner
from tests.synth import make_bars, snap, book, BAR_MS
from hlagent.data import Bar

NOW = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)  # a Tuesday


def sig(side="long", entry=60_000.0, stop_pct=0.5, s=None):
    if s is not None:
        entry = s.asks[0][0] if side == "long" else s.bids[0][0]
    stop = entry * (1 - stop_pct / 100) if side == "long" else entry * (1 + stop_pct / 100)
    tgt = entry + (entry - stop) * 1.5
    return Signal("imbalance", "BTC", side, entry, stop, tgt, 0.8, "t")


def engine(tmp_path, **kw):
    return RiskEngine(RiskConfig(**kw), str(tmp_path / "risk.json"), 400.0)


def test_sizing_respects_risk_and_leverage(tmp_path):
    e = engine(tmp_path)
    s = snap(make_bars(40))
    d = e.check(sig(s=s), s, 0, NOW)
    assert d.allowed
    assert abs(d.risk_usd - 400 * 0.0075) < 1e-6
    assert d.notional <= 400 * 3 + 1e-6
    # very tight stop would imply huge notional -> leverage cap binds
    d2 = e.check(sig(stop_pct=0.16, s=s), s, 0, NOW)
    assert d2.allowed and abs(d2.notional - 1200) < 1e-6


def test_daily_loss_blocks(tmp_path):
    e = engine(tmp_path)
    e.record_result(-11.0, NOW)        # 2.75% > 2.5%
    s = snap(make_bars(40))
    d = e.check(sig(s=s), s, 0, NOW)
    assert not d.allowed and "daily loss" in d.reason


def test_streak_cooldown_then_release(tmp_path):
    e = engine(tmp_path, max_daily_loss_pct=50, max_weekly_loss_pct=50)
    for _ in range(3):
        e.record_result(-1.0, NOW)
    s = snap(make_bars(40))
    assert "cooldown" in e.check(sig(s=s), s, 0, NOW).reason
    assert e.check(sig(s=s), s, 0, NOW + timedelta(hours=3)).allowed


def test_drawdown_halts_permanently(tmp_path):
    e = engine(tmp_path, max_daily_loss_pct=50, max_weekly_loss_pct=50, max_consecutive_losses=99)
    e.record_result(-45.0, NOW)        # 11.25% dd
    s = snap(make_bars(40))
    assert "drawdown" in e.check(sig(), s, 0, NOW).reason
    e2 = RiskEngine(RiskConfig(), str(tmp_path / "risk.json"), 400.0)   # survives restart
    assert not e2.check(sig(), s, 0, NOW).allowed


def test_market_gates(tmp_path):
    e = engine(tmp_path)
    s = snap(make_bars(40))
    s.asks = [(s.mid * 1.001, 2.0)] + s.asks[1:]           # ~10bps spread
    assert "spread" in e.check(sig(s=s), s, 0, NOW).reason
    s = snap(make_bars(40), funding=0.001)                 # 0.1%/8h
    assert "crowded" in e.check(sig("long", s=s), s, 0, NOW).reason
    assert e.check(sig("short", stop_pct=0.5, s=s), s, 0, NOW).allowed
    s = snap(make_bars(40))
    s.bids, s.asks = book(s.mid, 0.0001, 0.0001)
    assert "thin" in e.check(sig(), s, 0, NOW).reason
    s = snap(make_bars(40)); m = s.mid
    assert "through stop" in e.check(Signal("x", "BTC", "long", m, m * 1.001, m * 1.01, 1, ""), s, 0, NOW).reason
    assert "stale" in e.check(Signal("x", "BTC", "long", m * 1.01, m * 0.99, m * 1.02, 1, ""), s, 0, NOW).reason


def test_halt_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    e = engine(tmp_path)
    (tmp_path / "HALT").write_text("")
    assert "halt file" in e.check(sig(), snap(make_bars(40)), 0, NOW).reason


def test_paper_broker_stop_and_target():
    b = PaperBroker()
    bars = make_bars(40, vol=0.0001)
    s = snap(bars)
    pos = b.open(sig(entry=s.mid), 0.01, s)
    assert pos.entry > s.mid  # paid the ask + slippage
    nxt = bars + [Bar(bars[-1].t + BAR_MS, s.mid, s.mid, pos.stop * 0.999, s.mid, 10),
                  Bar(bars[-1].t + 2 * BAR_MS, s.mid, s.mid, s.mid, s.mid, 1)]
    fills = b.mark(snap(nxt), 12, 0.8)
    assert len(fills) == 1 and fills[0].exit_reason == "stop" and fills[0].pnl_usd < 0


def test_learner_gates_and_tunes(tmp_path):
    from hlagent.execution import Fill, Position
    L = Learner(str(tmp_path), StrategyConfig(), seed=0)
    L.EXPLORE_RATE = 0.0
    for i in range(30):
        pos = Position(str(i), "BTC", "long", 0.01, 100, 99, 101.5, "imbalance", i, meta={"imbalance": 0.4})
        L.record(Fill(pos, 99, -0.01, 0.0, "stop"), "RANGE")
    takes = sum(L.should_take("imbalance", "RANGE")[0] for _ in range(200))
    assert takes < 10, "losing arm should be mostly skipped"
    assert 0 < L.should_take("stop_run", "RANGE")[1] < 1
    assert (tmp_path / "journal.jsonl").exists() and (tmp_path / "arms.json").exists()


def test_agent_end_to_end_paper(tmp_path):
    st = Settings(risk=RiskConfig(), strategy=StrategyConfig(imbalance_confirm_ticks=1),
                  mode="paper", state_dir=str(tmp_path), starting_equity=400)
    agent = Agent(st, seed=1)
    bars = make_bars(150, drift=0.002, vol=0.0005, seed=3)   # uptrend
    out = agent.step(snap(bars, bid_mult=3.0), NOW)
    assert any(a.startswith("OPEN long") for a in out["actions"]), out
    assert len(agent.broker.positions) == 1
    # second signal blocked by max_open_positions
    out2 = agent.step(snap(bars, bid_mult=3.0), NOW)
    assert any("max open positions" in a for a in out2["actions"]), out2
    # walk price to target
    pos = next(iter(agent.broker.positions.values()))
    more = bars + [Bar(bars[-1].t + BAR_MS, pos.entry, pos.target * 1.001, pos.entry, pos.target, 10),
                   Bar(bars[-1].t + 2 * BAR_MS, pos.target, pos.target, pos.target, pos.target, 1)]
    out3 = agent.step(snap(more, bid_mult=1.0), NOW)
    assert any("CLOSE long" in a and "target" in a for a in out3["actions"]), out3
    assert agent.risk.state.equity > 400
    assert (tmp_path / "journal.jsonl").read_text().count("\n") == 1

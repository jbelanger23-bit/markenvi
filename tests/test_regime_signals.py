from hlagent.config import StrategyConfig
from hlagent.data import Bar
from hlagent.regime import classify, TransitionMatrix
from hlagent.signals import ImbalanceDetector, StopRunDetector, LiquidationRunDetector
from tests.synth import make_bars, snap, BAR_MS


def test_trend_up_classified():
    bars = make_bars(150, drift=0.002, vol=0.0005, seed=3)
    r = classify(snap(bars))
    assert r.state == "TREND_UP" and r.bias > 0


def test_range_classified():
    bars = make_bars(150, drift=0.0, vol=0.0003, seed=5)
    r = classify(snap(bars))
    assert r.state in ("RANGE", "VOLATILE")


def test_liquidation_classified():
    bars = make_bars(150, vol=0.0005, seed=7)
    last = bars[-2]
    bars[-2] = Bar(last.t, last.o, last.o, last.o * 0.96, last.o * 0.965, 400.0)
    r = classify(snap(bars, oi=9_800, oi_prev=10_000))
    assert r.state == "LIQUIDATION"


def test_transition_matrix(tmp_path):
    tm = TransitionMatrix(str(tmp_path / "t.json"))
    for s in ["RANGE", "RANGE", "TREND_UP", "TREND_UP", "RANGE"]:
        tm.observe("BTC", s)
    p = tm.next_probs("RANGE")
    assert abs(sum(p.values()) - 1) < 1e-9 and p["TREND_UP"] > p["LIQUIDATION"]


def test_imbalance_needs_confirmation_and_regime_agreement():
    cfg = StrategyConfig(imbalance_confirm_ticks=3)
    det = ImbalanceDetector(cfg)
    bars = make_bars(150, drift=0.002, vol=0.0005, seed=3)   # TREND_UP
    s = snap(bars, bid_mult=3.0)                              # bid-heavy
    r = classify(s)
    assert det.detect(s, r) is None and det.detect(s, r) is None
    sig = det.detect(s, r)
    assert sig and sig.side == "long" and sig.r_multiple > 1
    # ask-heavy in an uptrend must be ignored
    det2 = ImbalanceDetector(cfg)
    s2 = snap(bars, ask_mult=3.0)
    assert all(det2.detect(s2, r) is None for _ in range(4))


def test_stop_run_long():
    cfg = StrategyConfig()
    bars = make_bars(60, vol=0.0003, seed=11)
    lo = min(b.l for b in bars[-26:-2])
    t = bars[-1].t
    bars[-2] = Bar(t - BAR_MS, lo * 1.001, lo * 1.002, lo * 0.997, lo * 0.999, 80)   # sweep
    bars[-1] = Bar(t, lo * 0.999, lo * 1.004, lo * 0.9985, lo * 1.003, 70)           # reclaim
    bars.append(Bar(t + BAR_MS, lo * 1.003, lo * 1.004, lo * 1.002, lo * 1.003, 10))  # partial
    s = snap(bars)
    sig = StopRunDetector(cfg).detect(s, classify(s))
    assert sig and sig.side == "long" and sig.stop < sig.entry < sig.target


def test_liq_run_long_after_flush():
    cfg = StrategyConfig()
    bars = make_bars(60, vol=0.0003, seed=13)
    t = bars[-1].t
    o = bars[-3].c
    bars[-2] = Bar(t - BAR_MS, o, o, o * 0.97, o * 0.972, 500)        # flush
    bars[-1] = Bar(t, o * 0.972, o * 0.99, o * 0.971, o * 0.985, 120)  # absorption
    bars.append(Bar(t + BAR_MS, o * 0.985, o * 0.986, o * 0.984, o * 0.985, 5))
    s = snap(bars, oi=9_700, oi_prev=10_000)
    r = classify(s)
    sig = LiquidationRunDetector(cfg).detect(s, r)
    assert sig and sig.side == "long" and sig.stop < bars[-3].l

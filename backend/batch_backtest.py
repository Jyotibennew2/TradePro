"""
TradePro Backend - Batch Backtest Engine (V1)

Runs many backtest combinations (strategies x instruments x timeframes x
expiries x strikes) in one call and ranks the results. This module does NOT
reimplement any backtest math — every job is executed via the existing,
already-tested calculation functions:

  - run_synthetic_backtest()   (backend/routes/backtest.py)  -> strategy/
    instrument/timeframe/SL/trailing-SL/greeks-filter sweeps
  - run_walkforward_backtest() (backend/routes/backtest.py)  -> real
    archived-data sweeps across expiries/strikes
  - GreeksEngine                (backend/greeks.py)          -> reused
    inside run_synthetic_backtest for the optional greeks_filter

Kept intentionally modular (BatchJob -> engine.run_synthetic_job /
run_walkforward_job -> rank()) so an Optimization mode (parameter search)
can later reuse the same job runners without touching this file's core.
"""

from dataclasses import dataclass, field
from itertools import product
from typing import Optional

from backend.routes.backtest import run_synthetic_backtest, run_walkforward_backtest

# Hard cap on how many job combinations one batch request can spawn, so a
# careless "all strategies x all symbols x all timeframes" body can't hang
# the server. V2/Optimization can raise this once batches run async.
MAX_JOBS = 60

RANK_METRICS = ("total_pnl", "roi_pct", "win_rate", "max_drawdown", "profit_factor", "risk_reward")


@dataclass
class BatchJob:
    """One combination to run. kind decides which existing engine executes it."""
    kind: str                       # "synthetic" | "walkforward"
    symbol: str
    strategy: Optional[str] = None
    resolution: Optional[str] = None
    expiry: Optional[str] = None
    legs: Optional[list] = field(default=None)
    entry_time: Optional[int] = None
    exit_time: Optional[int] = None


class BatchBacktestEngine:
    """
    Builds the cartesian product of the requested dimensions into BatchJob
    entries, executes each via the existing single-run engines, and ranks
    the results. Nothing here recomputes P&L/greeks — it only orchestrates
    and sorts.
    """

    def __init__(self, market, days: int = 90, lot_size: int = 50,
                 sl_pct: float = 50, tgt_pct: float = 50,
                 trailing_sl_pct: Optional[float] = None,
                 greeks_filter: Optional[dict] = None):
        self.market          = market
        self.days            = days
        self.lot_size        = lot_size
        self.sl_pct          = sl_pct
        self.tgt_pct         = tgt_pct
        self.trailing_sl_pct = trailing_sl_pct
        self.greeks_filter   = greeks_filter

    # -- job builders --------------------------------------------------

    def build_synthetic_jobs(self, strategies: list, symbols: list, resolutions: list) -> list[BatchJob]:
        combos = list(product(strategies, symbols, resolutions))[:MAX_JOBS]
        return [
            BatchJob(kind="synthetic", symbol=sym, strategy=strat, resolution=res)
            for strat, sym, res in combos
        ]

    def build_walkforward_jobs(self, symbols: list, expiries: list, strikes: list,
                                entry_time: int, exit_time: Optional[int] = None,
                                option_type: str = "CE", action: str = "BUY", lots: int = 1) -> list[BatchJob]:
        combos = list(product(symbols, expiries, strikes))[:MAX_JOBS]
        return [
            BatchJob(
                kind="walkforward", symbol=sym, expiry=exp,
                legs=[{"strike": strike, "option_type": option_type, "action": action, "lots": lots}],
                entry_time=entry_time, exit_time=exit_time,
            )
            for sym, exp, strike in combos
        ]

    # -- execution --------------------------------------------------

    def run(self, jobs: list[BatchJob]) -> list[dict]:
        results = []
        for job in jobs[:MAX_JOBS]:
            if job.kind == "synthetic":
                out = run_synthetic_backtest(
                    self.market, job.symbol, job.strategy, self.days, job.resolution,
                    self.sl_pct, self.tgt_pct, self.lot_size,
                    trailing_sl_pct=self.trailing_sl_pct, greeks_filter=self.greeks_filter,
                )
                if "error" in out:
                    results.append({"job": self._label(job), "error": out["error"]})
                    continue
                results.append({
                    "job": self._label(job),
                    "kind": "synthetic",
                    "symbol": job.symbol, "strategy": job.strategy, "resolution": job.resolution,
                    "summary": out["summary"],
                    "stopped_early": out["stopped_early"], "stop_reason": out["stop_reason"],
                })
            else:  # walkforward
                out = run_walkforward_backtest(
                    job.symbol, job.expiry, job.entry_time, job.legs,
                    self.lot_size, self.sl_pct, self.tgt_pct, job.exit_time,
                    trailing_sl_pct=self.trailing_sl_pct,
                )
                if "error" in out:
                    results.append({"job": self._label(job), "error": out["error"]})
                    continue
                summary = self._summary_from_equity(out["equity_curve"], out["final_pnl"])
                results.append({
                    "job": self._label(job),
                    "kind": "walkforward",
                    "symbol": job.symbol, "expiry": job.expiry, "legs": job.legs,
                    "summary": summary,
                    "exit_reason": out["exit"]["reason"],
                })
        return results

    def run_and_rank(self, jobs: list[BatchJob], rank_by: str = "total_pnl") -> dict:
        if rank_by not in RANK_METRICS:
            rank_by = "total_pnl"
        results = self.run(jobs)
        ok_results  = [r for r in results if "error" not in r]
        err_results = [r for r in results if "error" in r]

        # max_drawdown is negative-or-zero (worse = more negative), so for
        # ranking "best first" we sort ascending for that one metric only.
        reverse = rank_by != "max_drawdown"
        ok_results.sort(key=lambda r: r["summary"].get(rank_by, 0), reverse=reverse)
        for i, r in enumerate(ok_results, start=1):
            r["rank"] = i

        return {
            "rank_by"     : rank_by,
            "total_jobs"  : len(jobs),
            "ranked"      : ok_results,
            "failed"      : err_results,
        }

    # -- helpers --------------------------------------------------

    @staticmethod
    def _label(job: BatchJob) -> str:
        if job.kind == "synthetic":
            return f"{job.strategy}/{job.symbol}/{job.resolution}"
        strike = job.legs[0]["strike"] if job.legs else "?"
        return f"{job.symbol}/{job.expiry}/{strike}"

    @staticmethod
    def _summary_from_equity(equity_curve: list, final_pnl: float) -> dict:
        """
        Walk-forward jobs don't produce a per-trade list like the synthetic
        engine does (it's a single position replayed tick by tick), so build
        the same ranking-metric shape from its equity curve instead of
        reusing compute_summary_metrics (which expects a trades list).
        """
        if not equity_curve:
            return {"total_pnl": 0, "roi_pct": 0, "win_rate": 0, "max_drawdown": 0,
                    "profit_factor": 0, "risk_reward": 0}
        peak = equity_curve[0]["pnl"]
        mdd  = 0.0
        for pt in equity_curve:
            peak = max(peak, pt["pnl"])
            mdd  = min(mdd, pt["pnl"] - peak)
        return {
            "total_pnl"    : round(final_pnl, 2),
            "roi_pct"      : 0,   # needs entry premium/capital context not passed here in V1
            "win_rate"     : 100 if final_pnl > 0 else 0,
            "max_drawdown" : round(mdd, 2),
            "profit_factor": 0,
            "risk_reward"  : 0,
        }

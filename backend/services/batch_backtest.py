"""
TradePro Backend - Multi-scenario Batch Backtest Engine

Runs MANY backtest scenarios in one call - across expiries, strikes,
timeframes (entry frequency), and strategies - including Greeks-driven ones
that use the REAL archived delta/theta values at entry time (not just fixed
strike offsets) - and stores every result via
chain_archive.save_batch_results_bulk() so they can be ranked afterward
(best PnL / win-rate first).

PERFORMANCE: for a given (symbol, expiry_date, entry_time), the archived
snapshot series is fetched from SQLite exactly ONCE and reused across every
strategy x strike_offset combination tested at that entry point (via
chain_archive.simulate_legs_pnl_from_snapshots()) - re-fetching per
combination was the biggest cost driver in earlier runs, since a single
entry point might get tested by 5 strategies x 3 offsets = 15 combos.
Results are also bulk-inserted per entry point instead of one INSERT per
scenario.

STRATEGIES:
  straddle       - sell ATM (+ offset) call and put
  strangle       - sell OTM call and put, symmetric around ATM
  iron_condor    - sell a strangle, buy further OTM wings for defined risk
  delta_neutral  - Greeks-driven: sell the CE/PE strikes whose actual
                   archived delta is closest to +-0.30 (classic "30-delta
                   strangle" options-selling approach)
  theta_harvest  - Greeks-driven: among strikes near ATM, sell whichever
                   CE/PE combination has the highest combined |theta| at
                   entry (maximizes time-decay captured per day)
"""

import uuid
import logging
from datetime import datetime, timezone, timedelta

from backend.services import chain_archive

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_LOT_SIZES = {"NIFTY": 50, "BANKNIFTY": 15, "BTC": 1, "ETH": 1}

STRATEGIES = ("straddle", "strangle", "iron_condor", "delta_neutral", "theta_harvest")

# Entry-frequency labels -> minimum seconds between two chosen entry points.
# ("timeframe" in the batch-backtest sense = how often a fresh position is
# opened, not a candle resolution - archived data is always 5-min granular.)
TIMEFRAMES = {
    "5m" : 5 * 60,
    "15m": 15 * 60,
    "1h" : 60 * 60,
    "1d" : 24 * 60 * 60,
}

STRIKE_OFFSETS = (0, 1, 2)   # ATM, one step OTM, two steps OTM


def _atm_row(snapshot: dict) -> dict | None:
    for r in snapshot["rows"]:
        if r.get("atm"):
            return r
    return None


def _strike_step(snapshot: dict) -> float:
    strikes = sorted({r["strike"] for r in snapshot["rows"]})
    if len(strikes) < 2:
        return 100.0
    diffs = [b - a for a, b in zip(strikes, strikes[1:])]
    return min(d for d in diffs if d > 0) if any(d > 0 for d in diffs) else 100.0


def build_legs(strategy: str, snapshot: dict, offset: int, lots: int = 1) -> list[dict] | None:
    """
    Build the leg list for `strategy` using the ENTRY snapshot's real
    strikes/Greeks. `offset` (0, 1, 2, ...) picks how far OTM for the
    fixed-width strategies (straddle/strangle/iron_condor); the two
    Greeks-driven strategies ignore offset and instead scan the snapshot
    for the strike closest to a target delta, or with the highest theta.

    Returns None if the entry snapshot doesn't have suitable strikes/Greeks
    (e.g. Greeks weren't available for that capture, or too few strikes
    were archived to build the requested wing width).
    """
    atm_row = _atm_row(snapshot)
    if not atm_row:
        return None
    atm  = atm_row["strike"]
    step = _strike_step(snapshot)

    if strategy == "straddle":
        k = atm + offset * step
        return [
            {"strike": k, "option_type": "CE", "action": "SELL", "lots": lots},
            {"strike": k, "option_type": "PE", "action": "SELL", "lots": lots},
        ]

    if strategy == "strangle":
        ce_k = atm + (offset + 1) * step
        pe_k = atm - (offset + 1) * step
        return [
            {"strike": ce_k, "option_type": "CE", "action": "SELL", "lots": lots},
            {"strike": pe_k, "option_type": "PE", "action": "SELL", "lots": lots},
        ]

    if strategy == "iron_condor":
        wing = (offset + 2) * step
        body = (offset + 1) * step
        return [
            {"strike": atm + body, "option_type": "CE", "action": "SELL", "lots": lots},
            {"strike": atm + wing, "option_type": "CE", "action": "BUY",  "lots": lots},
            {"strike": atm - body, "option_type": "PE", "action": "SELL", "lots": lots},
            {"strike": atm - wing, "option_type": "PE", "action": "BUY",  "lots": lots},
        ]

    if strategy == "delta_neutral":
        target = 0.30
        ce_candidates = [r for r in snapshot["rows"] if r.get("ce_delta") is not None and r["strike"] >= atm]
        pe_candidates = [r for r in snapshot["rows"] if r.get("pe_delta") is not None and r["strike"] <= atm]
        if not ce_candidates or not pe_candidates:
            return None
        ce_row = min(ce_candidates, key=lambda r: abs(r["ce_delta"] - target))
        pe_row = min(pe_candidates, key=lambda r: abs(abs(r["pe_delta"]) - target))
        return [
            {"strike": ce_row["strike"], "option_type": "CE", "action": "SELL", "lots": lots},
            {"strike": pe_row["strike"], "option_type": "PE", "action": "SELL", "lots": lots},
        ]

    if strategy == "theta_harvest":
        near = [r for r in snapshot["rows"] if abs(r["strike"] - atm) <= 3 * step]
        ce_candidates = [r for r in near if r.get("ce_theta") is not None]
        pe_candidates = [r for r in near if r.get("pe_theta") is not None]
        if not ce_candidates or not pe_candidates:
            return None
        ce_row = max(ce_candidates, key=lambda r: abs(r["ce_theta"]))
        pe_row = max(pe_candidates, key=lambda r: abs(r["pe_theta"]))
        return [
            {"strike": ce_row["strike"], "option_type": "CE", "action": "SELL", "lots": lots},
            {"strike": pe_row["strike"], "option_type": "PE", "action": "SELL", "lots": lots},
        ]

    return None


def run_batch(symbols: list[str], strategies: list[str] | None = None,
              strike_offsets: list[int] | None = None, timeframes: list[str] | None = None,
              sl_pct: float = 50, tgt_pct: float = 50, lots: int = 1,
              max_entries_per_expiry: int = 20) -> dict:
    """
    Main entry point: loops symbol x expiry x timeframe x entry-point, and
    for EACH entry point fetches the archived snapshot series ONCE, then
    tries every strategy x strike_offset combo against that same fetched
    series (via simulate_legs_pnl_from_snapshots - no further DB reads).
    Results for the whole batch are bulk-inserted at the end.

    max_entries_per_expiry caps how many entry points are tried per
    (expiry, timeframe) pair - keeps a single call bounded even if months
    of 5-min data are archived.
    """
    strategies     = strategies or list(STRATEGIES)
    strike_offsets = strike_offsets if strike_offsets is not None else list(STRIKE_OFFSETS)
    timeframes     = timeframes or list(TIMEFRAMES.keys())

    batch_id = f"batch_{datetime.now(IST).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    total_run = total_saved = total_skipped = 0
    pending_rows: list[tuple] = []   # accumulated for one bulk insert per expiry

    for symbol in symbols:
        lot_size = DEFAULT_LOT_SIZES.get(symbol.upper(), 1)
        expiries = chain_archive.list_expiries(symbol)

        for expiry_date in expiries:
            all_times: list[int] = []
            for capture_date in chain_archive.list_available_dates(symbol, expiry_date):
                all_times += chain_archive.list_snapshot_times(symbol, expiry_date, capture_date)
            all_times = sorted(set(all_times))
            if not all_times:
                continue

            for tf_label in timeframes:
                tf_seconds = TIMEFRAMES.get(tf_label, 300)
                entries: list[int] = []
                last_t = None
                for t in all_times:
                    if last_t is None or t - last_t >= tf_seconds:
                        entries.append(t)
                        last_t = t
                entries = entries[:max_entries_per_expiry]

                for entry_t in entries:
                    # Fetch the archived series for this entry point ONCE -
                    # reused below for every strategy x offset combo.
                    snapshots = chain_archive.list_snapshots_range(symbol, expiry_date, entry_t)
                    if not snapshots:
                        continue
                    entry_snap = snapshots[0]

                    for strategy in strategies:
                        for offset in strike_offsets:
                            total_run += 1
                            legs = build_legs(strategy, entry_snap, offset, lots)
                            if not legs:
                                total_skipped += 1
                                continue
                            result = chain_archive.simulate_legs_pnl_from_snapshots(
                                snapshots, legs, lot_size, sl_pct, tgt_pct
                            )
                            if not result:
                                total_skipped += 1
                                continue
                            pending_rows.append((symbol, strategy, expiry_date, offset, tf_label, result))
                            total_saved += 1

                            # Flush periodically so a long-running batch still has
                            # partial results visible (and progress-queryable) before
                            # the whole thing finishes, without doing one INSERT per row.
                            if len(pending_rows) >= 200:
                                chain_archive.save_batch_results_bulk(batch_id, pending_rows)
                                pending_rows = []

    if pending_rows:
        chain_archive.save_batch_results_bulk(batch_id, pending_rows)

    logger.info(f"Batch {batch_id}: {total_saved} saved / {total_run} attempted ({total_skipped} skipped)")
    return {
        "batch_id"     : batch_id,
        "scenarios_run": total_run,
        "saved"        : total_saved,
        "skipped"      : total_skipped,
    }

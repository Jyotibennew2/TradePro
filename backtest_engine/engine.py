"""Backtest Engine — event-driven vectorised backtester."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pandas as pd


@dataclass
class Trade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: float
    direction: str   # long | short

    @property
    def pnl(self) -> float:
        if self.direction == "long":
            return (self.exit_price - self.entry_price) * self.quantity
        return (self.entry_price - self.exit_price) * self.quantity

    @property
    def pnl_pct(self) -> float:
        base = self.entry_price * self.quantity
        return (self.pnl / base * 100) if base else 0.0


@dataclass
class BacktestResult:
    symbol: str
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def win_rate(self) -> float:
        return (self.winning_trades / self.total_trades * 100) if self.total_trades else 0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def max_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for val in self.equity_curve:
            if val > peak:
                peak = val
            dd = (peak - val) / peak * 100 if peak else 0
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 2)

    @property
    def sharpe_ratio(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        returns = pd.Series(self.equity_curve).pct_change().dropna()
        if returns.std() == 0:
            return 0.0
        return round((returns.mean() / returns.std()) * (252 ** 0.5), 2)

    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "win_rate_pct": round(self.win_rate, 2),
            "total_pnl": round(self.total_pnl, 2),
            "max_drawdown_pct": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
        }


class BacktestEngine:
    """
    Runs a strategy function against historical OHLCV data.

    strategy_fn: Callable[[pd.DataFrame], pd.Series]
        Receives the OHLCV DataFrame and must return a Series of signals
        indexed like df, values in {1 (buy), -1 (sell), 0 (hold)}.
    """

    def __init__(
        self,
        strategy_fn: Callable[[pd.DataFrame], pd.Series],
        initial_capital: float = 100_000,
        commission_pct: float = 0.05,  # 0.05%
        slippage_pct: float = 0.02,
    ):
        self.strategy_fn = strategy_fn
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct / 100
        self.slippage_pct = slippage_pct / 100

    def run(self, symbol: str, df: pd.DataFrame) -> BacktestResult:
        """
        df: OHLCV DataFrame, DatetimeIndex, ascending order.
        """
        signals = self.strategy_fn(df)
        result = BacktestResult(symbol=symbol)
        capital = self.initial_capital
        position: Optional[dict] = None
        equity_curve = [capital]

        for i in range(1, len(df)):
            row = df.iloc[i]
            signal = signals.iloc[i - 1]  # signal generated from previous bar
            price = row["open"]  # execute on next bar open

            # Apply slippage
            exec_price = price * (1 + self.slippage_pct)

            if signal == 1 and position is None:
                # BUY
                qty = (capital * 0.95) / exec_price  # use 95% of capital
                commission = exec_price * qty * self.commission_pct
                capital -= exec_price * qty + commission
                position = {
                    "entry_price": exec_price,
                    "quantity": qty,
                    "entry_date": str(df.index[i]),
                }

            elif signal == -1 and position is not None:
                # SELL
                exit_price = price * (1 - self.slippage_pct)
                commission = exit_price * position["quantity"] * self.commission_pct
                proceeds = exit_price * position["quantity"] - commission
                capital += proceeds

                trade = Trade(
                    symbol=symbol,
                    entry_date=position["entry_date"],
                    exit_date=str(df.index[i]),
                    entry_price=position["entry_price"],
                    exit_price=exit_price,
                    quantity=position["quantity"],
                    direction="long",
                )
                result.trades.append(trade)
                position = None

            # Mark-to-market equity
            mtm = capital
            if position:
                mtm += row["close"] * position["quantity"]
            equity_curve.append(mtm)

        # Close any open position at last bar close
        if position:
            last_close = df["close"].iloc[-1]
            exit_price = last_close * (1 - self.slippage_pct)
            commission = exit_price * position["quantity"] * self.commission_pct
            capital += exit_price * position["quantity"] - commission
            trade = Trade(
                symbol=symbol,
                entry_date=position["entry_date"],
                exit_date=str(df.index[-1]),
                entry_price=position["entry_price"],
                exit_price=exit_price,
                quantity=position["quantity"],
                direction="long",
            )
            result.trades.append(trade)
            equity_curve[-1] = capital

        result.equity_curve = equity_curve
        return result

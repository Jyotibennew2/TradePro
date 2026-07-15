    # ------------------------------------------------------------------
    # Historical candles
    # ------------------------------------------------------------------

    def get_history(self, symbol: str, days: int = 90, resolution: str = "D") -> dict:
        """Returns live Fyers historical candles, or realistic mock candles."""
        fyers_symbol = SYMBOL_MAP.get(symbol.upper(), symbol)

        if self._client:
            try:
                to_ts   = int(time.time())
                from_ts = to_ts - days * 86400
                payload = {
                    "symbol"     : fyers_symbol,
                    "resolution" : resolution,
                    "date_format": "0",
                    "range_from" : str(from_ts),
                    "range_to"   : str(to_ts),
                    "cont_flag"  : "1",
                }
                resp = self._client.history(payload)
                if resp.get("code") == 200 or resp.get("s") == "ok":
                    raw = resp.get("candles", [])
                    if raw:
                        candles = [
                            {"t": c[0], "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[5]}
                            for c in raw
                        ]
                        return {"success": True, "mock": False, "candles": candles}
            except Exception as e:
                logger.error(f"History error: {e}")

        return self._mock_history(fyers_symbol, days, resolution)

    def _mock_history(self, symbol: str, days: int, resolution: str = "D") -> dict:
        """
        Generate realistic mock candles (random walk) when live data unavailable.
        resolution: "D" for daily candles, or minutes as a string ("5","15","30","60","120")
        for intraday candles — spaced accordingly instead of one-per-day.
        """
        base    = BASE_PRICES.get(symbol, 24300.0)
        price   = base * 0.90
        candles: list = []
        now     = int(time.time())

        if resolution == "D":
            step_seconds  = 86400
            candle_count  = days
            vol_per_candle= (500000, 2000000)
        else:
            minutes       = int(resolution)
            step_seconds  = minutes * 60
            # ~6.25 trading hours/day (375 min) worth of candles per day
            candles_per_day = max(1, 375 // minutes)
            candle_count    = days * candles_per_day
            vol_per_candle  = (5000, 50000)

        for i in range(candle_count, -1, -1):
            price  *= (1 + (random.random() - 0.49) * (0.015 if resolution == "D" else 0.004))
            high    = price * (1 + random.random() * (0.008 if resolution == "D" else 0.002))
            low     = price * (1 - random.random() * (0.008 if resolution == "D" else 0.002))
            open_   = price * (1 + (random.random() - 0.5) * (0.005 if resolution == "D" else 0.0015))
            volume  = int(random.uniform(*vol_per_candle))
            candles.append({
                "t"     : now - i * step_seconds,
                "open"  : round(open_, 2),
                "high"  : round(high,  2),
                "low"   : round(low,   2),
                "close" : round(price, 2),
                "volume": volume,
            })

        return {"success": True, "mock": True, "candles": candles}

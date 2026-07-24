import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, ReferenceDot } from "recharts";

const API = "http://192.0.0.4:8000/api";

// ===========================================================================
// Local Black-Scholes (mirrors backend/pricing.py exactly) — used for the
// Builder / Payoff / Greeks / Scenario tabs so they're instant, no network
// round-trip per keystroke. Hist Chain + Walk-Forward always use REAL
// archived data from the backend, never this.
// ===========================================================================

function erf(x) {
  const sign = x >= 0 ? 1 : -1;
  x = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * x);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return sign * y;
}
const normCDF = x => 0.5 * (1 + erf(x / Math.sqrt(2)));
const normPDF = x => Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);

function bsAll(S, K, T, r, sigma, type) {
  T = Math.max(T, 1e-9);
  sigma = Math.max(sigma, 1e-6);
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  const disc = Math.exp(-r * T);
  const isCall = type === "call" || type === "CE";
  const price = isCall
    ? S * normCDF(d1) - K * disc * normCDF(d2)
    : K * disc * normCDF(-d2) - S * normCDF(-d1);
  const delta = isCall ? normCDF(d1) : normCDF(d1) - 1;
  const gamma = normPDF(d1) / (S * sigma * sqrtT);
  const term1 = -(S * normPDF(d1) * sigma) / (2 * sqrtT);
  const term2 = isCall ? -r * K * disc * normCDF(d2) : r * K * disc * normCDF(-d2);
  const theta = (term1 + term2) / 365;
  const vega = (S * normPDF(d1) * sqrtT) / 100;
  return { price: round2(price), delta: +delta.toFixed(3), gamma: +gamma.toFixed(5), theta: +theta.toFixed(2), vega: +vega.toFixed(2) };
}
const round2 = n => Math.round(n * 100) / 100;
const atmOf = (spot, step = 100) => Math.round(spot / step) * step;

// ---------------------------------------------------------------------------
// Strategy template -> legs generator (all computed locally via BS)
// ---------------------------------------------------------------------------

function buildTemplateLegs(name, S, T, r, iv, width) {
  const atm = atmOf(S);
  const leg = (action, type, K) => {
    const g = bsAll(S, K, T, r, iv, type === "CE" ? "call" : "put");
    return { id: Math.random().toString(36).slice(2, 9), action, type, strike: K, premium: g.price, lots: 1 };
  };
  switch (name) {
    case "Long Call": return [leg("BUY", "CE", atm)];
    case "Long Put": return [leg("BUY", "PE", atm)];
    case "Covered Call": return [leg("SELL", "CE", atm + width)];
    case "Bull Call Spread": return [leg("BUY", "CE", atm), leg("SELL", "CE", atm + width)];
    case "Bear Put Spread": return [leg("BUY", "PE", atm), leg("SELL", "PE", atm - width)];
    case "Bull Put Spread": return [leg("SELL", "PE", atm), leg("BUY", "PE", atm - width)];
    case "Bear Call Spread": return [leg("SELL", "CE", atm), leg("BUY", "CE", atm + width)];
    case "Short Straddle": return [leg("SELL", "CE", atm), leg("SELL", "PE", atm)];
    case "Long Straddle": return [leg("BUY", "CE", atm), leg("BUY", "PE", atm)];
    case "Short Strangle": return [leg("SELL", "CE", atm + width), leg("SELL", "PE", atm - width)];
    case "Long Strangle": return [leg("BUY", "CE", atm + width), leg("BUY", "PE", atm - width)];
    case "Iron Condor": return [
      leg("SELL", "CE", atm + width), leg("BUY", "CE", atm + width * 2),
      leg("SELL", "PE", atm - width), leg("BUY", "PE", atm - width * 2),
    ];
    case "Iron Butterfly": return [
      leg("SELL", "CE", atm), leg("SELL", "PE", atm),
      leg("BUY", "CE", atm + width), leg("BUY", "PE", atm - width),
    ];
    case "Jade Lizard": return [
      leg("SELL", "PE", atm - width), leg("SELL", "CE", atm + width), leg("BUY", "CE", atm + width * 2),
    ];
    case "Broken Wing Butterfly": return [
      leg("BUY", "CE", atm - width), leg("SELL", "CE", atm), leg("SELL", "CE", atm),
      leg("BUY", "CE", atm + width * 2),
    ];
    case "Ratio Spread": return [
      leg("BUY", "CE", atm), leg("SELL", "CE", atm + width), leg("SELL", "CE", atm + width),
    ];
    default: return [];
  }
}

const TEMPLATES = [
  { name: "Long Call", tag: "Bullish", legs: 1 },
  { name: "Long Put", tag: "Bearish", legs: 1 },
  { name: "Covered Call", tag: "Neutral", legs: 1 },
  { name: "Bull Call Spread", tag: "Bullish", legs: 2 },
  { name: "Bear Put Spread", tag: "Bearish", legs: 2 },
  { name: "Short Straddle", tag: "Sideways", legs: 2 },
  { name: "Long Straddle", tag: "Volatile", legs: 2 },
  { name: "Short Strangle", tag: "Sideways", legs: 2 },
  { name: "Long Strangle", tag: "Volatile", legs: 2 },
  { name: "Iron Condor", tag: "Sideways", legs: 4 },
  { name: "Iron Butterfly", tag: "Sideways", legs: 4 },
  { name: "Jade Lizard", tag: "Bullish", legs: 3 },
  { name: "Broken Wing Butterfly", tag: "Neutral", legs: 3 },
  { name: "Bull Put Spread", tag: "Bullish", legs: 2 },
  { name: "Bear Call Spread", tag: "Bearish", legs: 2 },
  { name: "Ratio Spread", tag: "Neutral", legs: 3 },
];

const TIMEFRAMES = [
  { label: "5min", min: 5 }, { label: "15min", min: 15 }, { label: "30min", min: 30 },
  { label: "1hr", min: 60 }, { label: "2hr", min: 120 }, { label: "1day", min: 1440 },
];
const SPEEDS = [0.5, 1, 2, 5];

// ---------------------------------------------------------------------------
// Colors (matches TradePro.jsx theme)
// ---------------------------------------------------------------------------
const C = {
  bg: "#03050d", panel: "#060c1a", panel2: "#090f1e", border: "#0f1e36", border2: "#1a3050",
  text: "#c0d0e8", muted: "#445566", muted2: "#334455", cyan: "#00c8f0", green: "#00d97e",
  red: "#f03060", purple: "#9b5cf6", orange: "#f0a030",
};

function Btn({ children, onClick, active, danger, style, small }) {
  return (
    <button onClick={onClick} style={{
      background: active ? C.cyan : C.panel2, color: active ? "#03050d" : (danger ? C.red : C.text),
      border: `1px solid ${active ? C.cyan : C.border2}`, borderRadius: 8,
      padding: small ? "4px 8px" : "7px 12px", fontSize: small ? 10 : 11, fontWeight: 700,
      fontFamily: "monospace", cursor: "pointer", ...style,
    }}>{children}</button>
  );
}

function Field({ label, value, onChange, type = "text", suffix }) {
  return (
    <div style={{ flex: 1, minWidth: 110 }}>
      <div style={{ fontSize: 10, color: C.muted, marginBottom: 3 }}>{label}</div>
      <div style={{ display: "flex", alignItems: "center", background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 8, padding: "6px 10px" }}>
        <input value={value} type={type} onChange={e => onChange(e.target.value)}
          style={{ background: "none", border: "none", outline: "none", color: C.cyan, fontFamily: "monospace", fontSize: 13, fontWeight: 700, width: "100%" }} />
        {suffix && <span style={{ color: C.muted, fontSize: 10, marginLeft: 4 }}>{suffix}</span>}
      </div>
    </div>
  );
}

function Section({ title, icon, children }) {
  return (
    <div style={{ borderTop: `1px solid ${C.border}`, padding: "14px 12px" }}>
      <div style={{ fontSize: 12, fontWeight: 800, color: C.cyan, marginBottom: 10, letterSpacing: 0.5 }}>
        {icon} {title}
      </div>
      {children}
    </div>
  );
}

// ===========================================================================
// Main component
// ===========================================================================

export default function Simulator() {
  // ---- Market parameters ----
  const [underlying, setUnderlying] = useState("NIFTY");
  const [spot, setSpot] = useState(24300);
  const [ivPct, setIvPct] = useState(15);
  const [daysToExpiry, setDaysToExpiry] = useState(7);
  const [ratePct, setRatePct] = useState(6.5);

  const T = Math.max(daysToExpiry, 0.5) / 365;
  const r = ratePct / 100;
  const iv = ivPct / 100;

  // ---- Builder ----
  const [legs, setLegs] = useState([]);
  const [strategyLabel, setStrategyLabel] = useState("");
  const [strategyName, setStrategyName] = useState("My Strategy");
  const [savedStrategies, setSavedStrategies] = useState([]);

  useEffect(() => {
    try { setSavedStrategies(JSON.parse(localStorage.getItem("tradepro_saved_strategies") || "[]")); } catch { /* ignore */ }
  }, []);

  const applyTemplate = (name) => {
    const newLegs = buildTemplateLegs(name, spot, T, r, iv, 200);
    setLegs(newLegs);
    setStrategyLabel(name);
  };

  const addLeg = (action, type) => {
    const atm = atmOf(spot);
    const g = bsAll(spot, atm, T, r, iv, type === "CE" ? "call" : "put");
    setLegs(l => [...l, { id: Math.random().toString(36).slice(2, 9), action, type, strike: atm, premium: g.price, lots: 1 }]);
  };

  const updateLeg = (id, patch) => setLegs(l => l.map(x => x.id === id ? { ...x, ...patch } : x));
  const removeLeg = (id) => setLegs(l => l.filter(x => x.id !== id));

  // re-price legs whenever market params change (keeps premiums live)
  useEffect(() => {
    setLegs(l => l.map(leg => {
      const g = bsAll(spot, leg.strike, T, r, iv, leg.type === "CE" ? "call" : "put");
      return { ...leg, premium: g.price };
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spot, ivPct, daysToExpiry, ratePct]);

  const saveStrategy = () => {
    const entry = { name: strategyName, legs, spot, iv: ivPct, days: daysToExpiry, rate: ratePct, savedAt: Date.now() };
    const updated = [...savedStrategies.filter(s => s.name !== strategyName), entry];
    setSavedStrategies(updated);
    localStorage.setItem("tradepro_saved_strategies", JSON.stringify(updated));
  };
  const loadStrategy = (s) => {
    setLegs(s.legs); setSpot(s.spot); setIvPct(s.iv); setDaysToExpiry(s.days); setRatePct(s.rate); setStrategyName(s.name);
  };
  const deleteStrategy = (name) => {
    const updated = savedStrategies.filter(s => s.name !== name);
    setSavedStrategies(updated);
    localStorage.setItem("tradepro_saved_strategies", JSON.stringify(updated));
  };
  const exportStrategy = () => {
    const blob = new Blob([JSON.stringify({ name: strategyName, legs, spot, iv: ivPct, days: daysToExpiry, rate: ratePct }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = `${strategyName || "strategy"}.json`; a.click();
    URL.revokeObjectURL(url);
  };
  const importStrategy = (file) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const s = JSON.parse(reader.result);
        setLegs(s.legs || []); setStrategyName(s.name || "Imported");
        if (s.spot) setSpot(s.spot); if (s.iv) setIvPct(s.iv); if (s.days) setDaysToExpiry(s.days); if (s.rate) setRatePct(s.rate);
      } catch { /* ignore bad file */ }
    };
    reader.readAsText(file);
  };

  // ---- Net entry cost / credit-debit (BUY=+cost, SELL=-cost/credit) ----
  const netCost = useMemo(() => legs.reduce((sum, l) => sum + (l.action === "BUY" ? l.premium : -l.premium) * l.lots, 0), [legs]);
  const isCredit = netCost < 0;

  // ---- Payoff at expiry across a spot range ----
  const payoffData = useMemo(() => {
    if (legs.length === 0) return [];
    const lotSize = { NIFTY: 50, BANKNIFTY: 15, MIDCPNIFTY: 75 }[underlying] || 50;
    const lo = spot * 0.9, hi = spot * 1.1;
    const pts = [];
    for (let i = 0; i <= 60; i++) {
      const Sx = lo + (hi - lo) * (i / 60);
      let pnl = 0;
      for (const l of legs) {
        const intrinsic = l.type === "CE" ? Math.max(Sx - l.strike, 0) : Math.max(l.strike - Sx, 0);
        const legPnl = (l.action === "BUY" ? (intrinsic - l.premium) : (l.premium - intrinsic)) * l.lots * lotSize;
        pnl += legPnl;
      }
      pts.push({ spot: Math.round(Sx), pnl: round2(pnl) });
    }
    return pts;
  }, [legs, spot, underlying]);

  const maxProfit = payoffData.length ? Math.max(...payoffData.map(p => p.pnl)) : 0;
  const maxLoss = payoffData.length ? Math.min(...payoffData.map(p => p.pnl)) : 0;
  const breakevens = useMemo(() => {
    const out = [];
    for (let i = 1; i < payoffData.length; i++) {
      const a = payoffData[i - 1], b = payoffData[i];
      if ((a.pnl < 0 && b.pnl >= 0) || (a.pnl >= 0 && b.pnl < 0)) {
        out.push(Math.round(a.spot + (b.spot - a.spot) * (0 - a.pnl) / (b.pnl - a.pnl)));
      }
    }
    return out;
  }, [payoffData]);

  // ---- Aggregate Greeks ----
  const aggGreeks = useMemo(() => {
    const lotSize = { NIFTY: 50, BANKNIFTY: 15, MIDCPNIFTY: 75 }[underlying] || 50;
    let d = 0, g = 0, t = 0, v = 0;
    for (const l of legs) {
      const gg = bsAll(spot, l.strike, T, r, iv, l.type === "CE" ? "call" : "put");
      const sign = l.action === "BUY" ? 1 : -1;
      d += sign * gg.delta * l.lots * lotSize;
      g += sign * gg.gamma * l.lots * lotSize;
      t += sign * gg.theta * l.lots * lotSize;
      v += sign * gg.vega * l.lots * lotSize;
    }
    return { delta: round2(d), gamma: +g.toFixed(4), theta: round2(t), vega: round2(v) };
  }, [legs, spot, T, r, iv, underlying]);

  // ---- Approximate margin (NOT real SPAN — clearly labeled) ----
  const marginEst = useMemo(() => {
    const lotSize = { NIFTY: 50, BANKNIFTY: 15, MIDCPNIFTY: 75 }[underlying] || 50;
    let debit = 0, shortExposure = 0;
    for (const l of legs) {
      if (l.action === "BUY") debit += l.premium * l.lots * lotSize;
      else shortExposure += spot * l.lots * lotSize * 0.12; // rough 12% SPAN-like approximation
    }
    return round2(debit + shortExposure);
  }, [legs, spot, underlying]);

  // ---- Scenario (what-if) ----
  const [scenSpotPct, setScenSpotPct] = useState(0);
  const [scenIvPct, setScenIvPct] = useState(0);
  const [scenDaysElapsed, setScenDaysElapsed] = useState(0);
  const scenarioPnl = useMemo(() => {
    const lotSize = { NIFTY: 50, BANKNIFTY: 15, MIDCPNIFTY: 75 }[underlying] || 50;
    const Sx = spot * (1 + scenSpotPct / 100);
    const ivx = Math.max(0.01, iv * (1 + scenIvPct / 100));
    const Tx = Math.max((daysToExpiry - scenDaysElapsed), 0.25) / 365;
    let pnl = 0;
    for (const l of legs) {
      const g = bsAll(Sx, l.strike, Tx, r, ivx, l.type === "CE" ? "call" : "put");
      pnl += (l.action === "BUY" ? (g.price - l.premium) : (l.premium - g.price)) * l.lots * lotSize;
    }
    return round2(pnl);
  }, [legs, spot, iv, daysToExpiry, r, scenSpotPct, scenIvPct, scenDaysElapsed, underlying]);

  // ---- Adjust (simple rule-based suggestions) ----
  const adjustSuggestions = useMemo(() => {
    if (legs.length === 0) return [];
    const out = [];
    const nearestBE = breakevens.length ? breakevens.reduce((a, b) => Math.abs(b - spot) < Math.abs(a - spot) ? b : a) : null;
    if (nearestBE !== null && Math.abs(nearestBE - spot) / spot < 0.01) {
      out.push(`Spot ₹${spot} is within 1% of breakeven ₹${nearestBE} — position is at risk of flipping, consider rolling the threatened strike further OTM or booking out.`);
    }
    if (aggGreeks.theta < 0 && Math.abs(aggGreeks.theta) > Math.abs(netCost) * 0.15) {
      out.push(`Theta decay (₹${aggGreeks.theta}/day) is large relative to position size — time decay is working against you fast, review holding period.`);
    }
    if (isCredit && daysToExpiry <= 2) {
      out.push(`Only ${daysToExpiry} day(s) to expiry on a credit position — gamma risk is elevated, consider booking profit or tightening SL.`);
    }
    if (out.length === 0) out.push("No immediate red flags at current spot/IV — position looks structurally stable for now.");
    return out;
  }, [legs, breakevens, spot, aggGreeks, netCost, isCredit, daysToExpiry]);

  // ---- Hist Chain (real archived data) ----
  const [hcExpiries, setHcExpiries] = useState([]);
  const [hcDates, setHcDates] = useState([]);
  const [hcExpiry, setHcExpiry] = useState("");
  const [hcDate, setHcDate] = useState("");
  const [hcTimes, setHcTimes] = useState([]);
  const [hcTimeIdx, setHcTimeIdx] = useState(0);
  const [hcTimeframe, setHcTimeframe] = useState(5);
  const [hcRows, setHcRows] = useState([]);
  const [hcSpot, setHcSpot] = useState(0);
  const [hcSavedAt, setHcSavedAt] = useState(null);
  const [hcMock, setHcMock] = useState(true);
  const [hcPlaying, setHcPlaying] = useState(false);
  const [hcSpeed, setHcSpeed] = useState(1);
  const [hcSparkline, setHcSparkline] = useState([]);
  const playRef = useRef(null);

  useEffect(() => {
    fetch(`${API}/optionchain/archive/expiries?symbol=${underlying}`)
      .then(r => r.json()).then(d => { if (d.success) { setHcExpiries(d.expiries || []); if (!hcExpiry && d.expiries?.length) setHcExpiry(d.expiries[0]); } })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [underlying]);

  useEffect(() => {
    if (!hcExpiry) return;
    fetch(`${API}/optionchain/archive/dates?symbol=${underlying}&expiry=${hcExpiry}`)
      .then(r => r.json()).then(d => { if (d.success) { setHcDates(d.dates || []); if (!hcDate && d.dates?.length) setHcDate(d.dates[d.dates.length - 1]); } })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hcExpiry, underlying]);

  useEffect(() => {
    if (!hcExpiry || !hcDate) return;
    fetch(`${API}/optionchain/archive/times?symbol=${underlying}&date=${hcDate}&expiry=${hcExpiry}`)
      .then(r => r.json()).then(d => { if (d.success) { setHcTimes(d.times || []); setHcTimeIdx((d.times || []).length - 1); } })
      .catch(() => {});
    fetch(`${API}/historical?symbol=${underlying}&days=1&resolution=5m`)
      .then(r => r.json()).then(d => { if (d.candles) setHcSparkline(d.candles.map(c => ({ t: c.t, close: c.close }))); })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hcExpiry, hcDate, underlying]);

  const loadHcSnapshot = useCallback((epoch) => {
    if (!hcExpiry || !hcDate) return;
    fetch(`${API}/optionchain/archive?symbol=${underlying}&date=${hcDate}&expiry=${hcExpiry}&time=${epoch}`)
      .then(r => r.json())
      .then(d => {
        if (!d.success) return;
        setHcRows(d.data.expiryData || []);
        setHcSpot(d.spot || 0);
        setHcSavedAt(d.saved_at);
        setHcMock(d.was_mock);
      }).catch(() => {});
  }, [underlying, hcDate, hcExpiry]);

  useEffect(() => {
    if (hcTimes.length && hcTimeIdx >= 0 && hcTimeIdx < hcTimes.length) loadHcSnapshot(hcTimes[hcTimeIdx]);
  }, [hcTimes, hcTimeIdx, loadHcSnapshot]);

  const stepSnapshots = (dir) => {
    // archive captures roughly every 5 min -> convert selected timeframe to a snapshot count
    const stepCount = Math.max(1, Math.round(hcTimeframe / 5));
    setHcTimeIdx(i => {
      const next = i + dir * stepCount;
      return Math.max(0, Math.min(hcTimes.length - 1, next));
    });
  };

  useEffect(() => {
    if (hcPlaying) {
      playRef.current = setInterval(() => {
        setHcTimeIdx(i => {
          const stepCount = Math.max(1, Math.round(hcTimeframe / 5));
          const next = i + stepCount;
          if (next >= hcTimes.length) { setHcPlaying(false); return i; }
          return next;
        });
      }, 1500 / hcSpeed);
      return () => clearInterval(playRef.current);
    }
  }, [hcPlaying, hcSpeed, hcTimeframe, hcTimes.length]);

  const addLegFromChain = (row, type, action) => {
    const premium = type === "CE" ? row.ce_ltp : row.pe_ltp;
    if (premium == null) return;
    setLegs(l => [...l, { id: Math.random().toString(36).slice(2, 9), action, type, strike: row.strike, premium, lots: 1 }]);
  };

  const currentSparkDot = useMemo(() => {
    if (!hcSavedAt || !hcSparkline.length) return null;
    let best = hcSparkline[0], bestDiff = Infinity;
    for (const p of hcSparkline) {
      const diff = Math.abs(p.t - hcSavedAt);
      if (diff < bestDiff) { bestDiff = diff; best = p; }
    }
    return best;
  }, [hcSavedAt, hcSparkline]);

  // ---- Walk-Forward Backtest ----
  const [wfSlPct, setWfSlPct] = useState(50);
  const [wfTgtPct, setWfTgtPct] = useState(50);
  const [wfLoading, setWfLoading] = useState(false);
  const [wfResult, setWfResult] = useState(null);
  const [wfError, setWfError] = useState("");

  const runWalkForward = () => {
    if (!hcExpiry || !hcTimes.length || legs.length === 0) {
      setWfError("Builder mein legs add karo aur Hist Chain se ek expiry+date select karo pehle.");
      return;
    }
    setWfLoading(true); setWfError(""); setWfResult(null);
    const entryTime = hcTimes[hcTimeIdx];
    const lotSize = { NIFTY: 50, BANKNIFTY: 15, MIDCPNIFTY: 75 }[underlying] || 50;
    fetch(`${API}/backtest/walkforward`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: underlying, expiry: hcExpiry, entry_time: entryTime,
        legs: legs.map(l => ({ strike: l.strike, option_type: l.type, action: l.action, lots: l.lots })),
        lot_size: lotSize, sl_pct: wfSlPct, tgt_pct: wfTgtPct,
      }),
    }).then(r => r.json()).then(d => {
      if (d.success) setWfResult(d); else setWfError(d.error || "Backtest failed — is date/expiry ke liye aage ka data archive nahi hua abhi.");
    }).catch(() => setWfError("Request failed — server check karo.")).finally(() => setWfLoading(false));
  };

  // ===========================================================================
  const [openTabs, setOpenTabs] = useState(new Set(["builder"]));
  const toggle = (id) => setOpenTabs(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const TABS = [
    { id: "builder", label: "+ Builder" }, { id: "hist", label: "History Chain" },
    { id: "payoff", label: "Payoff" }, { id: "greeks", label: "Greeks" },
    { id: "scenario", label: "Scenario" }, { id: "margin", label: "Margin" },
    { id: "adjust", label: "Adjust" }, { id: "saved", label: "Saved" },
  ];

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "monospace", paddingBottom: 40 }}>
      {/* Header */}
      <div style={{ background: C.panel, borderBottom: `1px solid ${C.border}`, padding: "10px 16px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ fontSize: 16, fontWeight: 900, color: C.cyan }}>⚡ TradePro <span style={{ color: C.muted, fontWeight: 400, fontSize: 11 }}>Simulator</span></div>
        <div style={{ display: "flex", gap: 6 }}>
          {["NIFTY", "BANKNIFTY", "MIDCPNIFTY"].map(s => (
            <Btn key={s} small active={underlying === s} onClick={() => setUnderlying(s)}>{s}</Btn>
          ))}
        </div>
      </div>

      {/* Tab toggle bar */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, padding: "10px 12px", background: C.panel, borderBottom: `1px solid ${C.border}` }}>
        {TABS.map(t => <Btn key={t.id} small active={openTabs.has(t.id)} onClick={() => toggle(t.id)}>{t.label}</Btn>)}
      </div>

      {/* ================= BUILDER ================= */}
      {openTabs.has("builder") && (
        <Section title="Market Parameters" icon="📐">
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
            <Field label="Spot Price" value={spot} onChange={v => setSpot(+v || 0)} type="number" />
            <Field label="IV %" value={ivPct} onChange={v => setIvPct(+v || 0)} type="number" />
            <Field label="Days to Expiry" value={daysToExpiry} onChange={v => setDaysToExpiry(+v || 0)} type="number" />
            <Field label="Risk Free Rate %" value={ratePct} onChange={v => setRatePct(+v || 0)} type="number" />
          </div>

          <div style={{ fontSize: 11, color: C.muted, marginBottom: 6 }}>STRATEGY TEMPLATES</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 14 }}>
            {TEMPLATES.map(t => (
              <button key={t.name} onClick={() => applyTemplate(t.name)} style={{
                textAlign: "left", background: strategyLabel === t.name ? "#0d1f38" : C.panel2,
                border: `1px solid ${strategyLabel === t.name ? C.cyan : C.border}`, borderRadius: 8,
                padding: "8px 10px", cursor: "pointer", color: C.text, fontFamily: "monospace",
              }}>
                <div style={{ fontSize: 11, fontWeight: 700 }}>{t.name}</div>
                <div style={{ fontSize: 9, color: t.tag === "Bullish" ? C.green : t.tag === "Bearish" ? C.red : t.tag === "Volatile" ? C.purple : C.muted }}>
                  {t.tag} • {t.legs} leg{t.legs > 1 ? "s" : ""}
                </div>
              </button>
            ))}
          </div>

          <div style={{ fontSize: 11, color: C.muted, marginBottom: 6 }}>ADD LEGS</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 14 }}>
            <Btn onClick={() => addLeg("BUY", "CE")} style={{ color: C.green, borderColor: C.green + "40" }}>+ BUY CE</Btn>
            <Btn onClick={() => addLeg("SELL", "CE")} style={{ color: C.red, borderColor: C.red + "40" }}>+ SELL CE</Btn>
            <Btn onClick={() => addLeg("BUY", "PE")} style={{ color: C.green, borderColor: C.green + "40" }}>+ BUY PE</Btn>
            <Btn onClick={() => addLeg("SELL", "PE")} style={{ color: C.red, borderColor: C.red + "40" }}>+ SELL PE</Btn>
          </div>

          {legs.length > 0 && (
            <div style={{ marginBottom: 14 }}>
              {legs.map(l => (
                <div key={l.id} style={{ display: "grid", gridTemplateColumns: "50px 40px 70px 60px 40px 24px", gap: 4, alignItems: "center", padding: "6px 4px", borderBottom: `1px solid ${C.border}`, fontSize: 11 }}>
                  <span style={{ color: l.action === "BUY" ? C.green : C.red, fontWeight: 700 }}>{l.action}</span>
                  <span>{l.type}</span>
                  <input type="number" value={l.strike} onChange={e => updateLeg(l.id, { strike: +e.target.value })}
                    style={{ background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 4, color: C.cyan, width: 65, fontFamily: "monospace" }} />
                  <span style={{ color: C.muted }}>₹{l.premium}</span>
                  <input type="number" value={l.lots} min={1} onChange={e => updateLeg(l.id, { lots: Math.max(1, +e.target.value) })}
                    style={{ background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 4, color: C.text, width: 36, fontFamily: "monospace" }} />
                  <button onClick={() => removeLeg(l.id)} style={{ background: "none", border: "none", color: C.red, cursor: "pointer" }}>✕</button>
                </div>
              ))}
              <div style={{ marginTop: 8, fontSize: 12 }}>
                Net: <span style={{ color: isCredit ? C.green : C.red, fontWeight: 700 }}>
                  {isCredit ? "Credit" : "Debit"} ₹{Math.abs(netCost).toFixed(2)}
                </span>
              </div>
            </div>
          )}

          <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
            <input value={strategyName} onChange={e => setStrategyName(e.target.value)}
              style={{ flex: 1, background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 8, color: C.text, padding: "7px 10px", fontFamily: "monospace", fontSize: 12 }} />
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <Btn onClick={saveStrategy} style={{ color: C.green, flex: 1 }}>💾 Save</Btn>
            <Btn onClick={exportStrategy} style={{ color: C.cyan, flex: 1 }}>⬇ Export</Btn>
            <label style={{ flex: 1 }}>
              <Btn style={{ color: C.purple, width: "100%" }} onClick={() => document.getElementById("import-file").click()}>⬆ Import</Btn>
              <input id="import-file" type="file" accept="application/json" style={{ display: "none" }}
                onChange={e => e.target.files[0] && importStrategy(e.target.files[0])} />
            </label>
          </div>
        </Section>
      )}

      {/* ================= HISTORICAL OPTION CHAIN + WALK-FORWARD ================= */}
      {openTabs.has("hist") && (
        <Section title="Historical Option Chain (Real Archived Data)" icon="🕐">
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
            <div>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 3 }}>Expiry</div>
              <select value={hcExpiry} onChange={e => { setHcExpiry(e.target.value); setHcDate(""); }}
                style={{ background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 8, color: C.text, padding: "6px 8px", fontFamily: "monospace" }}>
                {hcExpiries.map(e => <option key={e} value={e}>{e}</option>)}
              </select>
            </div>
            <div>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 3 }}>Jump to date</div>
              <select value={hcDate} onChange={e => setHcDate(e.target.value)}
                style={{ background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 8, color: C.text, padding: "6px 8px", fontFamily: "monospace" }}>
                {hcDates.map(d => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>
          </div>

          {hcDates.length === 0 && hcExpiry && (
            <div style={{ color: C.orange, fontSize: 11, marginBottom: 10 }}>
              Is expiry ke liye abhi tak koi archived data nahi hai — scheduler har 5 min mein market hours ke dauraan data save karta hai, thoda wait karo.
            </div>
          )}

          {hcTimes.length > 0 && (
            <>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                {TIMEFRAMES.map(tf => <Btn key={tf.min} small active={hcTimeframe === tf.min} onClick={() => setHcTimeframe(tf.min)}>{tf.label}</Btn>)}
              </div>
              <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
                <Btn small onClick={() => stepSnapshots(-1)}>◀ Reverse</Btn>
                <Btn small active={hcPlaying} onClick={() => setHcPlaying(p => !p)}>{hcPlaying ? "⏸ Pause" : "▶ Auto"}</Btn>
                {SPEEDS.map(sp => <Btn key={sp} small active={hcSpeed === sp} onClick={() => setHcSpeed(sp)}>{sp}x</Btn>)}
                <Btn small onClick={() => stepSnapshots(1)}>Forward ▶</Btn>
              </div>

              {hcSparkline.length > 0 && (
                <div style={{ height: 60, marginBottom: 8 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={hcSparkline}>
                      <Line type="monotone" dataKey="close" stroke={C.cyan} dot={false} strokeWidth={1.5} isAnimationActive={false} />
                      {currentSparkDot && <ReferenceDot x={currentSparkDot.t} y={currentSparkDot.close} r={4} fill={C.orange} stroke="none" />}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}

              <div style={{ fontSize: 11, color: C.muted, marginBottom: 8 }}>
                {hcSavedAt ? new Date(hcSavedAt * 1000).toLocaleString("en-IN") : "—"} •
                Spot <span style={{ color: C.cyan, fontWeight: 700 }}>{hcSpot}</span> •
                <span style={{ color: hcMock ? C.orange : C.green }}> {hcMock ? "MOCK" : "REAL"}</span>
              </div>

              {hcRows.length > 0 && (
                <div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 70px 1fr 1fr", gap: 2, fontSize: 9, color: C.muted, textAlign: "center", marginBottom: 4 }}>
                    <div>B/S</div><div style={{ color: C.green }}>CE LTP</div><div style={{ color: C.cyan }}>STRIKE</div><div style={{ color: C.red }}>PE LTP</div><div>B/S</div>
                  </div>
                  {hcRows.map((row, i) => (
                    <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 1fr 70px 1fr 1fr", gap: 2, alignItems: "center", background: row.atm ? "#0d1f38" : "transparent", padding: "4px 2px", fontSize: 11 }}>
                      <div style={{ display: "flex", gap: 2, justifyContent: "center" }}>
                        <Btn small onClick={() => addLegFromChain(row, "CE", "BUY")} style={{ color: C.green, padding: "2px 5px" }}>B</Btn>
                        <Btn small onClick={() => addLegFromChain(row, "CE", "SELL")} style={{ color: C.red, padding: "2px 5px" }}>S</Btn>
                      </div>
                      <div style={{ textAlign: "center", color: C.green }}>{row.ce_ltp ?? "-"}</div>
                      <div style={{ textAlign: "center", fontWeight: 700, color: C.cyan }}>{row.strike}</div>
                      <div style={{ textAlign: "center", color: C.red }}>{row.pe_ltp ?? "-"}</div>
                      <div style={{ display: "flex", gap: 2, justifyContent: "center" }}>
                        <Btn small onClick={() => addLegFromChain(row, "PE", "BUY")} style={{ color: C.green, padding: "2px 5px" }}>B</Btn>
                        <Btn small onClick={() => addLegFromChain(row, "PE", "SELL")} style={{ color: C.red, padding: "2px 5px" }}>S</Btn>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Walk-Forward Backtest */}
              <div style={{ marginTop: 16, paddingTop: 14, borderTop: `1px dashed ${C.border}` }}>
                <div style={{ fontSize: 12, fontWeight: 800, color: C.orange, marginBottom: 8 }}>⚡ Real Walk-Forward Backtest</div>
                <div style={{ fontSize: 10, color: C.muted, marginBottom: 10 }}>
                  Builder ke legs ko is entry point (upar wale timestamp) se real archived prices ke through chalata hai — Black-Scholes nahi.
                </div>
                <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                  <Field label="SL %" value={wfSlPct} onChange={v => setWfSlPct(+v || 0)} type="number" />
                  <Field label="Target %" value={wfTgtPct} onChange={v => setWfTgtPct(+v || 0)} type="number" />
                </div>
                <Btn onClick={runWalkForward} style={{ color: C.orange, width: "100%" }}>
                  {wfLoading ? "Running..." : "Run Walk-Forward Backtest"}
                </Btn>
                {wfError && <div style={{ color: C.red, fontSize: 11, marginTop: 8 }}>{wfError}</div>}
                {wfResult && (
                  <div style={{ marginTop: 12 }}>
                    <div style={{ fontSize: 11, marginBottom: 6 }}>
                      Entry: {new Date(wfResult.entry.t * 1000).toLocaleString("en-IN")} @ spot {wfResult.entry.spot}<br />
                      Exit: {new Date(wfResult.exit.t * 1000).toLocaleString("en-IN")} @ spot {wfResult.exit.spot} — <span style={{ color: C.orange }}>{wfResult.exit.reason}</span><br />
                      Final P&L: <span style={{ color: wfResult.final_pnl >= 0 ? C.green : C.red, fontWeight: 700 }}>₹{wfResult.final_pnl}</span>
                      {wfResult.was_mock && <span style={{ color: C.orange }}> (mock data)</span>}
                    </div>
                    {wfResult.equity_curve.length > 1 && (
                      <div style={{ height: 140 }}>
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={wfResult.equity_curve.map(e => ({ time: new Date(e.t * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }), pnl: e.pnl }))}>
                            <CartesianGrid stroke={C.border} strokeDasharray="3 3" />
                            <XAxis dataKey="time" stroke={C.muted} fontSize={9} />
                            <YAxis stroke={C.muted} fontSize={9} />
                            <Tooltip contentStyle={{ background: C.panel, border: `1px solid ${C.border}`, fontSize: 11 }} />
                            <ReferenceLine y={0} stroke={C.muted2} />
                            <Line type="monotone" dataKey="pnl" stroke={C.orange} dot={false} strokeWidth={2} />
                          </LineChart>
                        </ResponsiveContainer>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </Section>
      )}

      {/* ================= PAYOFF ================= */}
      {openTabs.has("payoff") && (
        <Section title="Payoff Diagram (at Expiry)" icon="📈">
          {legs.length === 0 ? <div style={{ color: C.muted, fontSize: 11 }}>Builder mein legs add karo pehle.</div> : (
            <>
              <div style={{ display: "flex", gap: 14, marginBottom: 10, fontSize: 11 }}>
                <div>Max Profit: <span style={{ color: C.green, fontWeight: 700 }}>₹{maxProfit}</span></div>
                <div>Max Loss: <span style={{ color: C.red, fontWeight: 700 }}>₹{maxLoss}</span></div>
                <div>Breakeven: <span style={{ color: C.cyan, fontWeight: 700 }}>{breakevens.join(", ") || "—"}</span></div>
              </div>
              <div style={{ height: 220 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={payoffData}>
                    <CartesianGrid stroke={C.border} strokeDasharray="3 3" />
                    <XAxis dataKey="spot" stroke={C.muted} fontSize={9} />
                    <YAxis stroke={C.muted} fontSize={9} />
                    <Tooltip contentStyle={{ background: C.panel, border: `1px solid ${C.border}`, fontSize: 11 }} />
                    <ReferenceLine y={0} stroke={C.muted2} />
                    <ReferenceLine x={spot} stroke={C.purple} strokeDasharray="4 4" label={{ value: "Spot", fill: C.purple, fontSize: 9 }} />
                    <Line type="monotone" dataKey="pnl" stroke={C.cyan} dot={false} strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </>
          )}
        </Section>
      )}

      {/* ================= GREEKS ================= */}
      {openTabs.has("greeks") && (
        <Section title="Aggregate Greeks" icon="Σ">
          {legs.length === 0 ? <div style={{ color: C.muted, fontSize: 11 }}>Builder mein legs add karo pehle.</div> : (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {[["Delta", aggGreeks.delta, C.cyan], ["Gamma", aggGreeks.gamma, C.purple], ["Theta/day", aggGreeks.theta, C.red], ["Vega", aggGreeks.vega, C.green]].map(([label, val, color]) => (
                <div key={label} style={{ background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 8, padding: 12 }}>
                  <div style={{ fontSize: 10, color: C.muted }}>{label}</div>
                  <div style={{ fontSize: 18, fontWeight: 800, color }}>{val}</div>
                </div>
              ))}
            </div>
          )}
        </Section>
      )}

      {/* ================= SCENARIO ================= */}
      {openTabs.has("scenario") && (
        <Section title="What-If Scenario" icon="🔮">
          {legs.length === 0 ? <div style={{ color: C.muted, fontSize: 11 }}>Builder mein legs add karo pehle.</div> : (
            <>
              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 10, color: C.muted, marginBottom: 3 }}>Spot move: {scenSpotPct > 0 ? "+" : ""}{scenSpotPct}%</div>
                <input type="range" min={-10} max={10} step={0.5} value={scenSpotPct} onChange={e => setScenSpotPct(+e.target.value)} style={{ width: "100%" }} />
              </div>
              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 10, color: C.muted, marginBottom: 3 }}>IV change: {scenIvPct > 0 ? "+" : ""}{scenIvPct}%</div>
                <input type="range" min={-50} max={100} step={5} value={scenIvPct} onChange={e => setScenIvPct(+e.target.value)} style={{ width: "100%" }} />
              </div>
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 10, color: C.muted, marginBottom: 3 }}>Days elapsed: {scenDaysElapsed}</div>
                <input type="range" min={0} max={Math.max(daysToExpiry - 0.5, 0)} step={0.5} value={scenDaysElapsed} onChange={e => setScenDaysElapsed(+e.target.value)} style={{ width: "100%" }} />
              </div>
              <div style={{ fontSize: 13 }}>
                Projected P&L: <span style={{ color: scenarioPnl >= 0 ? C.green : C.red, fontWeight: 800, fontSize: 18 }}>₹{scenarioPnl}</span>
              </div>
            </>
          )}
        </Section>
      )}

      {/* ================= MARGIN ================= */}
      {openTabs.has("margin") && (
        <Section title="Margin Estimate" icon="🏦">
          {legs.length === 0 ? <div style={{ color: C.muted, fontSize: 11 }}>Builder mein legs add karo pehle.</div> : (
            <>
              <div style={{ fontSize: 20, fontWeight: 800, color: C.cyan, marginBottom: 6 }}>₹{marginEst.toLocaleString("en-IN")}</div>
              <div style={{ fontSize: 10, color: C.orange }}>
                Approximate only — buy legs use full premium, sell legs use a rough 12% of notional. Yeh real exchange SPAN/exposure margin nahi hai; apne broker ke margin calculator se hi final number lo.
              </div>
            </>
          )}
        </Section>
      )}

      {/* ================= ADJUST ================= */}
      {openTabs.has("adjust") && (
        <Section title="Adjustment Suggestions" icon="🛠">
          {legs.length === 0 ? <div style={{ color: C.muted, fontSize: 11 }}>Builder mein legs add karo pehle.</div> : (
            <div>
              {adjustSuggestions.map((s, i) => (
                <div key={i} style={{ background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 8, padding: 10, marginBottom: 8, fontSize: 11 }}>{s}</div>
              ))}
            </div>
          )}
        </Section>
      )}

      {/* ================= SAVED ================= */}
      {openTabs.has("saved") && (
        <Section title="Saved Strategies" icon="💾">
          {savedStrategies.length === 0 ? <div style={{ color: C.muted, fontSize: 11 }}>Koi saved strategy nahi hai — Builder mein Save dabao.</div> : (
            savedStrategies.map(s => (
              <div key={s.name} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 8, padding: 10, marginBottom: 6 }}>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700 }}>{s.name}</div>
                  <div style={{ fontSize: 9, color: C.muted }}>{s.legs.length} legs • {new Date(s.savedAt).toLocaleDateString("en-IN")}</div>
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  <Btn small onClick={() => loadStrategy(s)}>Load</Btn>
                  <Btn small danger onClick={() => deleteStrategy(s.name)}>Delete</Btn>
                </div>
              </div>
            ))
          )}
        </Section>
      )}
    </div>
  );
}

import { useState, useEffect, useCallback } from "react";
const API = "http://192.0.0.4:8000/api";

function useServerStatus() {
  const [s, setS] = useState({ ok: false, auth: false, mock: true });
  useEffect(() => {
    const c = () => fetch(API + "/health").then(r => r.json()).then(d => setS({ ok: true, auth: d.authenticated, mock: d.mock_mode })).catch(() => setS({ ok: false, auth: false, mock: true }));
    c(); const t = setInterval(c, 10000); return () => clearInterval(t);
  }, []);
  return s;
}

function useLiveQuotes() {
  const [q, setQ] = useState({});
  useEffect(() => {
    const l = () => fetch(API + "/quotes").then(r => r.json()).then(d => { if (d.success) setQ(d.data); }).catch(() => {});
    l(); const t = setInterval(l, 3000); return () => clearInterval(t);
  }, []);
  return q;
}

function parseChain(rawData, spot) {
  if (!rawData || !rawData.optionsChain) return { rows: [], atmIndex: 0 };
  const ceMap = {}, peMap = {};
  let spotPrice = spot;
  rawData.optionsChain.forEach(item => {
    if (item.option_type === "") { spotPrice = item.ltp; return; }
    const k = item.strike_price;
    if (item.option_type === "CE") ceMap[k] = item;
    else if (item.option_type === "PE") peMap[k] = item;
  });
  const strikes = [...new Set([...Object.keys(ceMap), ...Object.keys(peMap)])].map(Number).sort((a, b) => a - b);
  const atm = Math.round(spotPrice / 50) * 50;
  let atmIndex = 0;
  const rows = strikes.map((k, i) => {
    if (k === atm) atmIndex = i;
    const ce = ceMap[k] || {};
    const pe = peMap[k] || {};
    return { strike: k, ce_ltp: ce.ltp, pe_ltp: pe.ltp, ce_oi: ce.oi, pe_oi: pe.oi, ce_vol: ce.volume, pe_vol: pe.volume };
  });
  return { rows, atmIndex, spotPrice };
}

export default function TradePro() {
  const server = useServerStatus();
  const quotes = useLiveQuotes();
  const nifty = quotes["NSE:NIFTY50-INDEX"]?.ltp || 0;
  const bank = quotes["NSE:NIFTYBANK-INDEX"]?.ltp || 0;

  const [activeTab, setActiveTab] = useState("optionchain");
  const [underlying, setUnderlying] = useState("NIFTY");
  const [chain, setChain] = useState([]);
  const [atmIndex, setAtmIndex] = useState(0);
  const [spot, setSpot] = useState(0);
  const [expiries, setExpiries] = useState([]);
  const [expiry, setExpiry] = useState("");
  const [loading, setLoading] = useState(false);
  const [lastUpdate, setLastUpdate] = useState("");
  const [isMock, setIsMock] = useState(false);

  const fetchChain = useCallback(() => {
    setLoading(true);
    const url = expiry
      ? `${API}/optionchain?symbol=${underlying}&expiry=${expiry}`
      : `${API}/optionchain?symbol=${underlying}&expiry=`;
    fetch(url)
      .then(r => r.json())
      .then(d => {
        if (!d.success) return;
        setIsMock(d.mock || false);
        if (d.mock) {
          // Mock format
          const mockData = d.data;
          if (mockData && mockData.expiryData && Array.isArray(mockData.expiryData) && mockData.expiryData[0]?.strike) {
            setChain(mockData.expiryData);
            setAtmIndex(mockData.atmIndex || 0);
            setSpot(d.spot || 0);
          }
        } else {
          // Live Fyers format
          const raw = d.data;
          // Set expiries from expiryData
          if (raw.expiryData && raw.expiryData.length > 0) {
            const exList = raw.expiryData.map(e => ({ label: e.date, value: e.expiry }));
            setExpiries(exList);
            if (!expiry && exList.length > 0) setExpiry(exList[0].value);
          }
          // Parse optionsChain
          const spotVal = raw.optionsChain?.find(x => x.option_type === "")?.ltp || 0;
          setSpot(spotVal);
          const { rows, atmIndex: ai } = parseChain(raw, spotVal);
          setChain(rows);
          setAtmIndex(ai);
        }
        setLastUpdate(new Date().toLocaleTimeString("en-IN"));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [underlying, expiry]);

  useEffect(() => { fetchChain(); }, [fetchChain]);
  useEffect(() => {
    const t = setInterval(fetchChain, 5000);
    return () => clearInterval(t);
  }, [fetchChain]);

  const tabs = [
    { id: "optionchain", label: "📊 Chain" },
    { id: "paper", label: "📝 Paper" },
    { id: "portfolio", label: "💼 Portfolio" },
  ];

  return (
    <div style={{ minHeight: "100vh", background: "#03050d", color: "#c0d0e8", fontFamily: "monospace" }}>
      {/* Header */}
      <div style={{ background: "#060c1a", borderBottom: "1px solid #0f1e36", padding: "10px 16px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ fontSize: 18, fontWeight: 900, color: "#00c8f0" }}>⚡ TradePro</div>
        <div style={{ display: "flex", gap: 8, fontSize: 11, alignItems: "center" }}>
          <span style={{ background: "#090f1e", border: "1px solid #0f1e36", borderRadius: 8, padding: "4px 10px" }}>
            <span style={{ color: "#445566" }}>N </span>
            <span style={{ color: "#00c8f0", fontWeight: 700 }}>{nifty > 0 ? nifty.toLocaleString("en-IN") : "---"}</span>
          </span>
          <span style={{ background: "#090f1e", border: "1px solid #0f1e36", borderRadius: 8, padding: "4px 10px" }}>
            <span style={{ color: "#445566" }}>BN </span>
            <span style={{ color: "#9b5cf6", fontWeight: 700 }}>{bank > 0 ? bank.toLocaleString("en-IN") : "---"}</span>
          </span>
          <span style={{ color: server.ok ? (server.auth ? "#00d97e" : "#f0a030") : "#f03060", fontSize: 10 }}>
            ● {isMock ? "MOCK" : "LIVE"}
          </span>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", borderBottom: "1px solid #0f1e36", background: "#060c1a" }}>
        {tabs.map(t => (
          <button key={t.id} onClick={() => setActiveTab(t.id)}
            style={{ flex: 1, padding: "10px 4px", background: "none", border: "none", borderBottom: activeTab === t.id ? "2px solid #00c8f0" : "2px solid transparent", color: activeTab === t.id ? "#00c8f0" : "#445566", fontSize: 12, cursor: "pointer", fontFamily: "monospace" }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Option Chain */}
      {activeTab === "optionchain" && (
        <div style={{ padding: "10px 8px" }}>
          {/* Controls */}
          <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center", flexWrap: "wrap" }}>
            <div style={{ display: "flex", background: "#090f1e", border: "1px solid #0f1e36", borderRadius: 8, overflow: "hidden" }}>
              {["NIFTY", "BANKNIFTY"].map(s => (
                <button key={s} onClick={() => { setUnderlying(s); setExpiry(""); setChain([]); }}
                  style={{ padding: "6px 12px", background: underlying === s ? "#00c8f0" : "none", color: underlying === s ? "#03050d" : "#445566", border: "none", cursor: "pointer", fontSize: 10, fontWeight: 700, fontFamily: "monospace" }}>
                  {s}
                </button>
              ))}
            </div>
            {expiries.length > 0 && (
              <select value={expiry} onChange={e => setExpiry(e.target.value)}
                style={{ background: "#090f1e", border: "1px solid #0f1e36", borderRadius: 8, color: "#c0d0e8", padding: "5px 8px", fontSize: 10, fontFamily: "monospace" }}>
                {expiries.map(e => <option key={e.value} value={e.value}>{e.label}</option>)}
              </select>
            )}
            <span style={{ fontSize: 10, color: "#445566" }}>
              Spot: <span style={{ color: "#00c8f0", fontWeight: 700 }}>{spot > 0 ? spot.toLocaleString("en-IN") : "---"}</span>
            </span>
            <button onClick={fetchChain}
              style={{ marginLeft: "auto", background: "#0f1e36", border: "1px solid #1a3050", borderRadius: 8, color: "#00c8f0", padding: "6px 10px", fontSize: 10, cursor: "pointer" }}>
              🔄
            </button>
          </div>

          {lastUpdate && (
            <div style={{ fontSize: 9, color: "#334455", marginBottom: 6, textAlign: "right" }}>
              {lastUpdate} • auto 5s • {isMock ? "⚠️ MOCK" : "✅ LIVE"}
            </div>
          )}

          {loading && chain.length === 0 && (
            <div style={{ textAlign: "center", color: "#445566", padding: 30 }}>Loading...</div>
          )}

          {chain.length > 0 && (
            <div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 80px 1fr 1fr", gap: 2, marginBottom: 4, fontSize: 9, color: "#334455", textAlign: "center" }}>
                <div style={{ color: "#00d97e88" }}>CE OI</div>
                <div style={{ color: "#00d97e" }}>CE LTP</div>
                <div style={{ color: "#00c8f0" }}>STRIKE</div>
                <div style={{ color: "#f03060" }}>PE LTP</div>
                <div style={{ color: "#f0306088" }}>PE OI</div>
              </div>
              {chain.map((row, i) => {
                const isAtm = i === atmIndex;
                return (
                  <div key={i} style={{
                    display: "grid", gridTemplateColumns: "1fr 1fr 80px 1fr 1fr",
                    gap: 2, marginBottom: 1, borderRadius: 5,
                    background: isAtm ? "#0d1f38" : i % 2 === 0 ? "#060c1a" : "#070e1c",
                    border: isAtm ? "1px solid #00c8f040" : "1px solid transparent",
                    padding: "5px 2px", fontSize: 11
                  }}>
                    <div style={{ textAlign: "center", color: "#00d97e88" }}>
                      {row.ce_oi ? (row.ce_oi / 100000).toFixed(1) + "L" : "-"}
                    </div>
                    <div style={{ textAlign: "center", color: "#00d97e", fontWeight: isAtm ? 800 : 500 }}>
                      {row.ce_ltp != null ? "₹" + row.ce_ltp.toFixed(1) : "-"}
                    </div>
                    <div style={{ textAlign: "center", color: isAtm ? "#00c8f0" : "#8899aa", fontWeight: 700, fontSize: isAtm ? 12 : 11, background: isAtm ? "#00c8f015" : "none", borderRadius: 4 }}>
                      {row.strike}
                    </div>
                    <div style={{ textAlign: "center", color: "#f03060", fontWeight: isAtm ? 800 : 500 }}>
                      {row.pe_ltp != null ? "₹" + row.pe_ltp.toFixed(1) : "-"}
                    </div>
                    <div style={{ textAlign: "center", color: "#f0306088" }}>
                      {row.pe_oi ? (row.pe_oi / 100000).toFixed(1) + "L" : "-"}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {!loading && chain.length === 0 && (
            <div style={{ textAlign: "center", color: "#445566", padding: 30 }}>
              <div style={{ fontSize: 24, marginBottom: 8 }}>📭</div>
              <div>No data</div>
            </div>
          )}
        </div>
      )}

      {activeTab === "paper" && (
        <div style={{ padding: 20, textAlign: "center", color: "#445566" }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>📝</div>
          <div>Paper Trading — Coming Soon</div>
        </div>
      )}

      {activeTab === "portfolio" && (
        <div style={{ padding: 20, textAlign: "center", color: "#445566" }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>💼</div>
          <div>Portfolio — Coming Soon</div>
        </div>
      )}
    </div>
  );
}

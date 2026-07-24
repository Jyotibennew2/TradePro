import { useState } from "react";
import TradePro from "./TradePro";
import Simulator from "./Simulator";

function App() {
  const [page, setPage] = useState("dashboard");
  return (
    <div>
      <div style={{ display: "flex", background: "#060c1a", borderBottom: "1px solid #0f1e36" }}>
        <button onClick={() => setPage("dashboard")} style={{
          flex: 1, padding: "8px", background: "none", border: "none",
          borderBottom: page === "dashboard" ? "2px solid #00c8f0" : "2px solid transparent",
          color: page === "dashboard" ? "#00c8f0" : "#445566", fontFamily: "monospace", fontSize: 11, cursor: "pointer",
        }}>Dashboard</button>
        <button onClick={() => setPage("simulator")} style={{
          flex: 1, padding: "8px", background: "none", border: "none",
          borderBottom: page === "simulator" ? "2px solid #00c8f0" : "2px solid transparent",
          color: page === "simulator" ? "#00c8f0" : "#445566", fontFamily: "monospace", fontSize: 11, cursor: "pointer",
        }}>Simulator</button>
      </div>
      {page === "dashboard" ? <TradePro /> : <Simulator />}
    </div>
  );
}
export default App;

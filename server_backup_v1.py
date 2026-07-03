from flask import Flask, jsonify, request
from flask_cors import CORS
import time, os, math, random, hashlib
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)

APP_ID   = os.getenv("FYERS_APP_ID", "YOUR_APP_ID-100")
SECRET   = os.getenv("FYERS_SECRET_KEY", "YOUR_SECRET")
TOKEN    = os.getenv("FYERS_ACCESS_TOKEN", "")
REDIRECT = os.getenv("REDIRECT_URL", "http://127.0.0.1:8080/")
fyers_client = None

def get_client():
    global fyers_client
    if fyers_client is None and TOKEN:
        try:
            from fyers_apiv3 import fyersModel
            fyers_client = fyersModel.FyersModel(client_id=APP_ID, token=TOKEN, log_path="")
        except Exception as e:
            print("Fyers error:", e)
    return fyers_client

def bs(S, K, T, r, v, opt):
    if T <= 0 or v <= 0:
        return max((S-K) if opt=="call" else (K-S), 0)
    sq = math.sqrt(T)
    d1 = (math.log(S/K) + (r+0.5*v*v)*T) / (v*sq)
    d2 = d1 - v*sq
    N  = lambda x: 0.5*(1+math.erf(x/math.sqrt(2)))
    e  = math.exp(-r*T)
    return S*N(d1)-K*e*N(d2) if opt=="call" else K*e*N(-d2)-S*N(-d1)

@app.route("/api/health")
def health():
    return jsonify({"status":"ok","authenticated":bool(TOKEN),"mock_mode":not bool(TOKEN),"version":"2.0.0"})

@app.route("/api/auth/url")
def auth_url():
    try:
        from fyers_apiv3 import fyersModel
        s = fyersModel.SessionModel(client_id=APP_ID, secret_key=SECRET, redirect_uri=REDIRECT, response_type="code", grant_type="authorization_code")
        return jsonify({"success":True,"url":s.generate_authcode()})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)})

@app.route("/api/auth/token", methods=["POST"])
def auth_token():
    global TOKEN, fyers_client
    try:
        from fyers_apiv3 import fyersModel
        s = fyersModel.SessionModel(client_id=APP_ID, secret_key=SECRET, redirect_uri=REDIRECT, response_type="code", grant_type="authorization_code")
        s.set_token(request.json.get("auth_code",""))
        tok = s.generate_token().get("access_token","")
        if tok:
            TOKEN = tok
            fyers_client = None
            with open(".env","a") as f:
                f.write(f"\nFYERS_ACCESS_TOKEN={tok}")
            return jsonify({"success":True,"message":"Authenticated!"})
        return jsonify({"success":False,"error":"No token"})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)})

@app.route("/api/quotes")
def quotes():
    syms = request.args.get("symbols","NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX,NSE:NIFTYMID100-INDEX")
    client = get_client()
    if client:
        try:
            resp = client.quotes({"symbols":syms})
            if resp.get("code")==200:
                result={}
                for q in resp.get("d",[]):
                    v=q["v"]
                    result[q["n"]]={"ltp":v.get("lp",0),"ch":v.get("ch",0),"chp":v.get("chp",0),"vol":v.get("volume",0),"oi":v.get("oi",0)}
                return jsonify({"success":True,"data":result,"mock":False})
        except Exception as e:
            print("Quote error:",e)
    t = time.time()
    return jsonify({"success":True,"mock":True,"data":{
        "NSE:NIFTY50-INDEX":    {"ltp":round(22480+math.sin(t/30)*15+random.uniform(-5,5),2),"ch":45.2,"chp":0.20},
        "NSE:NIFTYBANK-INDEX":  {"ltp":round(58000+math.sin(t/25)*30+random.uniform(-10,10),2),"ch":-120.5,"chp":-0.25},
        "NSE:NIFTYMID100-INDEX":{"ltp":round(11940+math.sin(t/35)*10+random.uniform(-3,3),2),"ch":30.0,"chp":0.25}}})

@app.route("/api/optionchain")
def option_chain():
    symbol_map = {"NIFTY":"NSE:NIFTY50-INDEX","BANKNIFTY":"NSE:NIFTYBANK-INDEX","MIDCPNIFTY":"NSE:NIFTYMID100-INDEX"}
    raw = request.args.get("symbol","NIFTY")
    symbol = symbol_map.get(raw.upper(), raw)
    count  = int(request.args.get("strikecount","10"))
    client = get_client()
    if client:
        try:
            resp = client.optionchain({"symbol":symbol,"strikecount":count,"timestamp":""})
            if resp.get("code")==200 or resp.get("s")=="ok":
                return jsonify({"success":True,"data":resp.get("data",{}),"mock":False})
        except Exception as e:
            print("Chain error:",e)
    base = {"NSE:NIFTY50-INDEX":24300,"NSE:NIFTYBANK-INDEX":58000,"NSE:NIFTYMID100-INDEX":12800}
    spot = round(base.get(symbol,22480)+math.sin(time.time()/30)*base.get(symbol,22480)*0.001,2)
    step = 100 if spot<30000 else 200
    atm  = round(spot/step)*step
    rows = []
    for i in range(-count, count+1):
        K    = atm+i*step
        ce   = round(bs(spot,K,30/365,0.065,0.14,"call"),2)
        pe   = round(bs(spot,K,30/365,0.065,0.14,"put"),2)
        oi   = max(0.05,1-abs(K-spot)/(spot*0.12))*1200000
        h    = int(hashlib.md5(str(K).encode()).hexdigest(),16)%1000/1000
        skew = round(14+(1.5 if K<spot else -1)*(abs(K-spot)/spot)*80,1)
        rows.append({"strike":K,"ce_ltp":ce,"pe_ltp":pe,"ce_oi":int(oi*(0.7+0.6*h)),"pe_oi":int(oi*(0.7+0.6*(1-h))),"ce_vol":int(oi*0.4*h),"pe_vol":int(oi*0.4*(1-h)),"ce_iv":skew,"pe_iv":skew,"ce_delta":round(0.5-(K-spot)/(spot*0.3),3),"pe_delta":round(-0.5-(K-spot)/(spot*0.3),3),"atm":K==atm})
    return jsonify({"success":True,"mock":True,"spot":spot,"data":{"expiryData":rows,"atmIndex":count}})

@app.route("/api/positions")
def positions():
    client=get_client()
    if client:
        try: return jsonify({"success":True,"mock":False,"data":client.positions().get("netPositions",[])})
        except: pass
    return jsonify({"success":True,"mock":True,"data":[]})

@app.route("/api/orders")
def orders():
    client=get_client()
    if client:
        try: return jsonify({"success":True,"mock":False,"data":client.orderbook().get("orderBook",[])})
        except: pass
    return jsonify({"success":True,"mock":True,"data":[]})

@app.route("/api/funds")
def funds():
    client=get_client()
    if client:
        try:
            fl    = client.funds().get("fund_limit",[])
            total = next((f["equityAmount"] for f in fl if f.get("title")=="Total Balance"),0)
            used  = next((f["equityAmount"] for f in fl if f.get("title")=="Utilised Amount"),0)
            return jsonify({"success":True,"mock":False,"data":{"total":total,"used":used,"available":total-used}})
        except: pass
    return jsonify({"success":True,"mock":True,"data":{"total":500000,"used":0,"available":500000}})

@app.route("/api/backtest", methods=["POST"])
def backtest():
    b        = request.json or {}
    strategy = b.get("strategy","straddle")
    days     = int(b.get("days",90))
    sl_pct   = float(b.get("sl_pct",50))
    tgt_pct  = float(b.get("tgt_pct",50))
    lot_size = int(b.get("lot_size",50))
    p        = 22480*0.88
    candles  = []
    for i in range(days,-1,-1):
        p *= (1+(random.random()-0.47)*0.012)
        candles.append({"c":round(p,2),"t":int(time.time())-i*86400})
    trades=[]; rpnl=0; peak=0; mdd=0
    for day in candles:
        S=day["c"]; iv=0.13+random.random()*0.06; atm=round(S/100)*100; T=7/365; r=0.065
        if strategy=="straddle":    prem=bs(S,atm,T,r,iv,"call")+bs(S,atm,T,r,iv,"put")
        elif strategy=="strangle":  prem=bs(S,atm+200,T,r,iv,"call")+bs(S,atm-200,T,r,iv,"put")
        elif strategy=="ironCondor":prem=(bs(S,atm+200,T,r,iv,"call")-bs(S,atm+400,T,r,iv,"call"))+(bs(S,atm-200,T,r,iv,"put")-bs(S,atm-400,T,r,iv,"put"))
        elif strategy=="longCall":  prem=-bs(S,atm,T,r,iv,"call")
        else:                       prem=-bs(S,atm,T,r,iv,"put")
        if abs(prem)<0.5: continue
        move=(random.random()-0.5)*0.025
        if strategy in ["straddle","strangle","ironCondor"]:
            pnl=max(min(prem*lot_size*(0.6 if abs(move)<0.012 else -0.4)*(0.5+random.random()),prem*tgt_pct/100*lot_size),-prem*sl_pct/100*lot_size)
        else:
            ev=bs(S*(1+move),atm,max(T-1/365,0),r,iv*0.95,"call" if strategy=="longCall" else "put")
            pnl=max(min((ev-abs(prem))*lot_size,abs(prem)*tgt_pct/100*lot_size),-abs(prem)*sl_pct/100*lot_size)
        pnl=round(pnl,2); rpnl+=pnl; peak=max(peak,rpnl); mdd=min(mdd,rpnl-peak)
        trades.append({"date":datetime.fromtimestamp(day["t"]).strftime("%d %b"),"spot":round(S,2),"iv":round(iv*100,1),"prem":round(abs(prem),2),"pnl":pnl,"win":pnl>0})
    wins=[t for t in trades if t["win"]]; losses=[t for t in trades if not t["win"]]; tot=len(trades)
    eq=0; equity=[]
    for t in trades: eq+=t["pnl"]; equity.append({"date":t["date"],"equity":round(eq,2)})
    return jsonify({"success":True,"summary":{"total":tot,"wins":len(wins),"losses":len(losses),"win_rate":round(len(wins)/tot*100,1) if tot else 0,"total_pnl":round(rpnl,2),"max_drawdown":round(mdd,2),"avg_win":round(sum(t["pnl"] for t in wins)/len(wins),2) if wins else 0,"avg_loss":round(sum(t["pnl"] for t in losses)/len(losses),2) if losses else 0,"profit_factor":round(abs(sum(t["pnl"] for t in wins)/sum(t["pnl"] for t in losses)),2) if losses and sum(t["pnl"] for t in losses)!=0 else 0,"sharpe":round(rpnl/(abs(mdd)+1)*0.5,2)},"trades":trades[-50:],"equity_curve":equity})

if __name__ == "__main__":
    print("="*50)
    print("  TradePro Backend v2.0")
    print(f"  Mode   : {'LIVE' if TOKEN else 'MOCK'}")
    print(f"  Server : http://localhost:8000")
    print(f"  Health : http://localhost:8000/api/health")
    print("="*50)
    app.run(host="0.0.0.0", port=8000, debug=False, use_reloader=False)

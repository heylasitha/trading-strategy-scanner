"""Fast backtest — single symbol, last 3 signals"""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import yfinance as yf
from strategy import detect_sma_compression_breakout, detect_signal

SYMBOL   = "NVDA"
TFS      = ["1h", "4h", "1d"]
FWD_BARS = 10
MAX_SIG  = 3


def fetch(symbol, tf):
    imap = {"1h":"1h","4h":"1h","1d":"1d"}
    pmap = {"1h":"6mo","4h":"6mo","1d":"2y"}
    rmap = {"4h":"4h"}
    try:
        df = yf.Ticker(symbol).history(period=pmap[tf], interval=imap[tf], auto_adjust=True)
        if df is None or df.empty: return None
        df.columns = [c.lower() for c in df.columns]
        df = df[["open","high","low","close","volume"]].astype(float)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        if tf in rmap:
            df = df.resample(rmap[tf]).agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()
        df = df[~df.index.duplicated(keep="last")]
        df.sort_index(inplace=True)
        return df
    except Exception as e:
        print(f"fetch error: {e}")
        return None


def outcome(df, i, entry, stop, target):
    risk = entry - stop
    if risk <= 0: return "SKIP", 0
    future = df.iloc[i+1:i+1+FWD_BARS]
    if len(future) == 0: return "OPEN", 0
    hit_t = (future["high"] >= target).any()
    hit_s = (future["low"]  <= stop).any()
    if hit_t and hit_s:
        ti = future[future["high"] >= target].index[0]
        si = future[future["low"]  <= stop].index[0]
        return ("WIN", round((target-entry)/risk,2)) if ti<=si else ("LOSS",-1.0)
    if hit_t: return "WIN",  round((target-entry)/risk,2)
    if hit_s: return "LOSS", -1.0
    pnl = round((float(future["close"].iloc[-1])-entry)/risk,2)
    return "OPEN", pnl


trades = []

for tf in TFS:
    print(f"Scanning NVDA {tf}...", flush=True)
    df = fetch(SYMBOL, tf)
    if df is None or len(df) < 240:
        print(f"  Not enough data")
        continue

    count = 0
    for i in range(len(df)-FWD_BARS-2, 229, -1):
        if count >= MAX_SIG: break
        window = df.iloc[:i+1].copy()

        sig = detect_sma_compression_breakout(window, SYMBOL, tf)
        if sig:
            res, pnl = outcome(df, i, sig["entry"], sig["stop"], sig["target"])
            trades.append({"tf":tf,"date":str(df.index[i].date()),"type":"COMPRESSION",
                "structure":sig.get("structure","?"),"strength":sig["strength"],
                "rr":sig["rr"],"outcome":res,"pnl_r":pnl})
            count += 1
            continue

        sig2 = detect_signal(window, SYMBOL, tf)
        if sig2 and "VWAP" in sig2.get("pattern",""):
            res, pnl = outcome(df, i, sig2["entry"], sig2["stop"], sig2["target"])
            trades.append({"tf":tf,"date":str(df.index[i].date()),"type":"VWAP",
                "structure":sig2.get("pattern",""),"strength":sig2["strength"],
                "rr":sig2["rr"],"outcome":res,"pnl_r":pnl})
            count += 1

if not trades:
    print("\nNo signals found for NVDA in recent history.")
else:
    trades.sort(key=lambda t: t["date"])
    wins   = [t for t in trades if t["outcome"]=="WIN"]
    losses = [t for t in trades if t["outcome"]=="LOSS"]
    total_r = sum(t["pnl_r"] for t in trades)
    closed  = len(wins)+len(losses)
    wr      = len(wins)/closed*100 if closed else 0

    print(f"\n{'='*60}")
    print(f"  NVDA — Last {len(trades)} signals")
    print(f"{'='*60}")
    print(f"  Wins     : {len(wins)}   Losses: {len(losses)}   WR: {wr:.0f}%")
    print(f"  Total R  : {total_r:+.1f}R")
    print(f"\n  {'TF':<4} {'DATE':<12} {'TYPE':<12} {'STRUCTURE':<24} {'RR':<5} {'RESULT':<5} P&L")
    print(f"  {'-'*70}")
    for t in trades:
        icon = "✅" if t["outcome"]=="WIN" else ("❌" if t["outcome"]=="LOSS" else "⏳")
        print(f"  {t['tf']:<4} {t['date']:<12} {t['type']:<12} {t['structure']:<24} {t['rr']:<5} {icon}  {t['pnl_r']:+.2f}R")

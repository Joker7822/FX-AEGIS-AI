"""Read-only, offline-ready, one-screen dashboard for the FX multi-agent backtester.

Only binds to localhost by default. It does not fetch live prices or place orders.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT = Path(__file__).resolve().parent
ASSETS = PROJECT / "dashboard_ui"


def read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def number(v):
    try:
        n = float(v)
        return n if n == n and abs(n) != float("inf") else None
    except (TypeError, ValueError):
        return None


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def epoch(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def build_payload(results: Path, market: Path, segment: str, research: Path | None = None,
                  evolution_report: Path | None = None) -> dict:
    summary = read_json(results / "summary.json")
    part = "validation" if segment == "validation" else "test"
    stats = summary.get(f"{part}_20pct", {})
    decisions = read_csv(results / f"{part}_decisions.csv")
    trades = read_csv(results / f"{part}_trades.csv")
    equity = read_csv(results / f"{part}_equity.csv")
    market_rows = read_csv(market)
    start, end = epoch(stats.get("start_utc")), epoch(stats.get("end_utc"))
    if start is not None and end is not None:
        # Include 200 bars of prehistory so moving averages are meaningful.
        selected = [i for i, r in enumerate(market_rows) if start <= (epoch(r.get("time")) or -1) <= end + 10800]
        if selected:
            a, b = max(0, selected[0] - 200), min(len(market_rows), selected[-1] + 1)
            market_rows = market_rows[a:b]
        else:
            market_rows = []
    market_data = []
    for r in market_rows:
        v = number(r.get("close"))
        if epoch(r.get("time")) is not None and v is not None:
            market_data.append({"t": r["time"], "o": number(r.get("open")), "h": number(r.get("high")), "l": number(r.get("low")), "c": v})
    research_data = {}
    if research is not None and research.is_file():
        try:
            research_data = read_json(research)
        except (OSError, ValueError, TypeError):
            research_data = {}
    evolution = {}
    if evolution_report is not None and evolution_report.is_file():
        try:
            evolution = read_json(evolution_report)
        except (OSError, ValueError, TypeError):
            evolution = {}
    hits = research_data.get("trusted_official_hits", [])
    if not isinstance(hits, list):
        hits = []
    last_signal = decisions[-1] if decisions else {}
    try:
        custom_votes = json.loads(last_signal.get("custom_votes_json") or "{}")
        if not isinstance(custom_votes, dict):
            custom_votes = {}
    except (TypeError, ValueError):
        custom_votes = {}
    last_time = max((p.stat().st_mtime for p in (results / "summary.json", results / f"{part}_decisions.csv") if p.is_file()), default=None)
    return {
        "segment": part,
        "stats": stats,
        "last_signal": {
            "time": last_signal.get("signal_time_utc"),
            "side": int(number(last_signal.get("model_side")) or 0),
            "raw_side": int(number(last_signal.get("raw_council_side")) or 0),
            "trend": number(last_signal.get("p_up_trend")),
            "reversion": number(last_signal.get("p_up_reversion")),
            "volatility": number(last_signal.get("predicted_abs_return")),
            "vol_cap": number(last_signal.get("volatility_cap")),
            "vol_veto": last_signal.get("volatility_veto") == "True",
            "filter": last_signal.get("filter_reason", ""),
            "execution": last_signal.get("execution_result", ""),
            "event_veto": last_signal.get("event_veto") == "True",
            "custom_veto": last_signal.get("custom_veto") == "True",
            "custom_votes": custom_votes,
        },
        "decisions": [{"t":r.get("signal_time_utc"), "side":int(number(r.get("model_side")) or 0), "p1":number(r.get("p_up_trend")), "p2":number(r.get("p_up_reversion")), "reason":r.get("execution_result","")} for r in decisions[-7:]][::-1],
        "trades": [{"t":r.get("exit_time_utc"), "entry":r.get("entry_time_utc"), "side":r.get("side"), "pnl":number(r.get("net_pnl_yen")), "equity":number(r.get("equity_after_yen"))} for r in trades[-8:]][::-1],
        "trade_markers": [{"t":r.get("entry_time_utc"), "side":r.get("side")} for r in trades],
        "equity": [{"t":r.get("time_utc"), "v":number(r.get("equity_yen"))} for r in equity],
        "market": market_data,
        "start":stats.get("start_utc"),
        "end":stats.get("end_utc"),
        "summary": {
            "strategy":summary.get("strategy"),
            "custom_agents":summary.get("custom_agents", []),
            "evolved_agents":summary.get("evolved_agents", []),
            "events_loaded":summary.get("events_loaded",0),
            "event_file":summary.get("event_file"),
            "research_screen":summary.get("research_screen",{}),
            "gates":summary.get("trade_gates",{}),
            "config":summary.get("config",{}),
            "notes":summary.get("notes",[]),
        },
        "evolution": {
            "available": bool(evolution),
            "candidate_name": (evolution.get("selected_blueprint") or {}).get("name"),
            "candidate_count": evolution.get("candidate_count"),
            "promotion_eligible": evolution.get("promotion_eligible") is True,
            "paper_promoted": evolution.get("paper_promoted") is True,
            "validation_net_yen": (evolution.get("validation") or {}).get("net_yen"),
            "double_cost_net_yen": (evolution.get("validation") or {}).get("stress_2x_net_yen"),
        },
        "online_research": {
            "retrieved_at_utc": research_data.get("retrieved_at_utc"),
            "official_hit_count": len(hits),
            "last_headline": (hits[0].get("title", "")[:160] if hits and isinstance(hits[0], dict) else None),
            "available": bool(research_data),
        },
        "file_updated_utc": datetime.fromtimestamp(last_time, timezone.utc).isoformat() if last_time else None,
        "has_market":bool(market_data),
    }


def make_handler(results:Path, market:Path, data_label:str, research:Path|None=None,
                 evolution_report:Path|None=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url=urlparse(self.path)
            if url.path == "/api/dashboard":
                part=parse_qs(url.query).get("segment",["test"])[0]
                if part not in ("test", "validation"):
                    return self.respond(400, b'{"error":"Invalid segment"}', "application/json")
                try:
                    payload=build_payload(results,market,part,research,evolution_report)
                    payload["data_label"]=data_label
                    self.respond(200,json.dumps(payload,ensure_ascii=False,allow_nan=False).encode("utf-8"),"application/json; charset=utf-8")
                except (OSError,ValueError,KeyError,TypeError) as exc:
                    self.respond(500,json.dumps({"error":str(exc)}).encode(),"application/json")
            else:
                assets={"/":"index.html","/index.html":"index.html","/app.js":"app.js","/styles.css":"styles.css"}
                if url.path not in assets:
                    return self.respond(404,b"Not found","text/plain")
                p=ASSETS/assets[url.path]
                if not p.is_file():
                    return self.respond(404,b"Not found","text/plain")
                mime={".js":"text/javascript; charset=utf-8",".html":"text/html; charset=utf-8",".css":"text/css; charset=utf-8"}
                self.respond(200,p.read_bytes(),mime[p.suffix])

        def respond(self,status:int,body:bytes,content_type:str):
            self.send_response(status)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(body)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            # Local API polling does not need per-request console spam.
            if self.path != "/api/dashboard":
                super().log_message(fmt,*args)
    return Handler


def main():
    ap=argparse.ArgumentParser(description="Read-only one-screen FX backtest dashboard")
    ap.add_argument("--results",type=Path,default=PROJECT/"results"/"demo_trend")
    ap.add_argument("--market",type=Path,default=PROJECT/"data"/"demo_synthetic_usdjpy.csv")
    ap.add_argument("--research",type=Path,default=PROJECT/"data"/"official_web_search.json",help="Optional output of research_agent.py search")
    ap.add_argument("--data-label",default=None,help="Provenance of the loaded input, displayed prominently")
    ap.add_argument("--evolution-report",type=Path,default=PROJECT/"results"/"demo_evolution"/"evolution_report.json")
    ap.add_argument("--port",type=int,default=8501)
    ap.add_argument("--host",default="127.0.0.1",help="Defaults to localhost for safety")
    args=ap.parse_args()
    if not (args.results/"summary.json").is_file():
        ap.error(f"summary.json not found under {args.results}; run main.py first")
    build_payload(args.results,args.market,"test",args.research,args.evolution_report)
    demo = (PROJECT/"data"/"demo_synthetic_usdjpy.csv").resolve()
    data_label = args.data_label or ("人工生成データ / DEMO" if args.market.resolve() == demo else "入力データ / 出典未検証")
    server=ThreadingHTTPServer((args.host,args.port),make_handler(args.results,args.market,data_label,args.research,args.evolution_report))
    print(f"FX Dashboard: http://{args.host}:{args.port} (read-only; no real orders)")
    print("Set browser full-screen (F11) to fit on one display. Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=="__main__":
    main()

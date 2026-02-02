#!/usr/bin/env python3
"""God Candle Scanner - Binance + Bybit websocket/REST hybrid.

Streams live klines over websockets for fast detection and uses REST APIs
for symbol discovery and initial historical candles.
"""

import argparse
import asyncio
import json
import logging
import random
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Dict, Iterable, List, Optional, Tuple

import aiohttp
import numpy as np
from rich.console import Console
from rich.live import Live
from rich.table import Table


BINANCE_REST = "https://api.binance.com"
BINANCE_WS = "wss://stream.binance.com:9443/stream"
BYBIT_REST = "https://api.bybit.com"
BYBIT_WS = "wss://stream.bybit.com/v5/public/spot"

USER_AGENT = "GodCandleScanner/2.0"
DEFAULT_LOOKBACK = 250

console = Console()


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool


@dataclass
class SymbolState:
    candles: Deque[Candle] = field(default_factory=lambda: deque(maxlen=DEFAULT_LOOKBACK))
    forming: Optional[Candle] = None
    last_alert_time: Optional[datetime] = None


class RateLimiter:
    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delta = now - self._last_call
            if delta < self.min_interval:
                await asyncio.sleep(self.min_interval - delta)
            self._last_call = time.monotonic()


class RestClient:
    def __init__(self, session: aiohttp.ClientSession, limiter: RateLimiter) -> None:
        self.session = session
        self.limiter = limiter

    async def get_json(self, url: str, params: Optional[Dict[str, str]] = None) -> Dict:
        backoff = 1.0
        for _ in range(6):
            await self.limiter.wait()
            try:
                async with self.session.get(url, params=params) as response:
                    if response.status in {418, 429}:
                        raise aiohttp.ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response.status,
                            message="rate limited",
                        )
                    response.raise_for_status()
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                await asyncio.sleep(backoff + random.random())
                backoff = min(backoff * 2, 15)
                last_exc = exc
        raise RuntimeError(f"REST request failed after retries: {url}") from last_exc


class ExchangeClient:
    def __init__(self, rest: RestClient) -> None:
        self.rest = rest

    async def discover_symbols(self, top_n: int) -> List[str]:
        raise NotImplementedError

    async def fetch_klines(self, symbol: str, interval: str, limit: int) -> List[Candle]:
        raise NotImplementedError


class BinanceClient(ExchangeClient):
    async def discover_symbols(self, top_n: int) -> List[str]:
        info = await self.rest.get_json(f"{BINANCE_REST}/api/v3/exchangeInfo")
        tradable = {
            item["symbol"]
            for item in info["symbols"]
            if item["status"] == "TRADING" and item["quoteAsset"] == "USDT"
        }
        tickers = await self.rest.get_json(f"{BINANCE_REST}/api/v3/ticker/24hr")
        ranked = [t for t in tickers if t["symbol"] in tradable]
        ranked.sort(key=lambda t: float(t.get("quoteVolume", 0.0)))
        return [t["symbol"] for t in ranked[:top_n]]

    async def fetch_klines(self, symbol: str, interval: str, limit: int) -> List[Candle]:
        params = {"symbol": symbol, "interval": interval, "limit": str(limit)}
        data = await self.rest.get_json(f"{BINANCE_REST}/api/v3/klines", params=params)
        candles = []
        for row in data:
            timestamp = datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc)
            candles.append(
                Candle(
                    timestamp=timestamp,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    closed=True,
                )
            )
        return candles


class BybitClient(ExchangeClient):
    async def discover_symbols(self, top_n: int) -> List[str]:
        params = {"category": "spot"}
        data = await self.rest.get_json(f"{BYBIT_REST}/v5/market/tickers", params=params)
        tickers = data.get("result", {}).get("list", [])
        usdt = [t for t in tickers if t.get("symbol", "").endswith("USDT")]
        usdt.sort(key=lambda t: float(t.get("turnover24h", 0.0)))
        return [t["symbol"] for t in usdt[:top_n]]

    async def fetch_klines(self, symbol: str, interval: str, limit: int) -> List[Candle]:
        interval_map = {
            "1m": "1",
            "3m": "3",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "2h": "120",
            "4h": "240",
            "1d": "D",
        }
        bybit_interval = interval_map.get(interval)
        if not bybit_interval:
            raise ValueError(f"Unsupported interval for Bybit: {interval}")
        params = {
            "category": "spot",
            "symbol": symbol,
            "interval": bybit_interval,
            "limit": str(limit),
        }
        data = await self.rest.get_json(f"{BYBIT_REST}/v5/market/kline", params=params)
        rows = data.get("result", {}).get("list", [])
        candles = []
        for row in rows:
            timestamp = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
            candles.append(
                Candle(
                    timestamp=timestamp,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    closed=True,
                )
            )
        candles.sort(key=lambda c: c.timestamp)
        return candles


def build_alert_table(alerts: List[Dict[str, str]]) -> Table:
    table = Table(title="God Candle Alerts", expand=True)
    table.add_column("Time", style="dim")
    table.add_column("Exchange")
    table.add_column("Symbol")
    table.add_column("Price", justify="right")
    table.add_column("Impulse%", justify="right")
    table.add_column("Vol x", justify="right")
    table.add_column("Break", justify="left")
    table.add_column("Notes")

    for alert in alerts[-20:][::-1]:
        table.add_row(
            alert["time"],
            alert["exchange"],
            alert["symbol"],
            alert["price"],
            alert["impulse"],
            alert["volume"],
            alert["break"],
            alert["notes"],
        )
    return table


def compute_signal(candles: Deque[Candle], args: argparse.Namespace) -> Optional[Dict[str, str]]:
    if len(candles) < max(args.lookback_min, args.impulse_window + 2, 60):
        return None

    closes = np.array([c.close for c in candles], dtype=float)
    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    volumes = np.array([c.volume for c in candles], dtype=float)

    window_start = -1 - args.impulse_window
    base_price = closes[window_start]
    if base_price == 0:
        return None
    impulse_pct = ((closes[-1] - base_price) / base_price) * 100

    vol_window = volumes[-20:]
    vol_ma = vol_window.mean() if len(vol_window) == 20 else 0.0
    vol_multiple = volumes[-1] / vol_ma if vol_ma > 0 else 0.0

    if len(closes) < 30:
        return None
    bb_window = closes[-20:]
    bb_mean = bb_window.mean()
    bb_std = bb_window.std(ddof=0)
    if bb_mean == 0:
        return None
    bb_width = (4 * bb_std) / bb_mean

    width_samples = []
    for idx in range(-10, 0):
        sub = closes[idx - 19 : idx + 1]
        if len(sub) < 20:
            continue
        sub_mean = sub.mean()
        sub_std = sub.std(ddof=0)
        if sub_mean == 0:
            continue
        width_samples.append((4 * sub_std) / sub_mean)
    width_ma10 = np.mean(width_samples) if width_samples else 0.0

    swing_high = highs[-51:-1].max() if len(highs) >= 51 else highs[:-1].max()

    structure_breaks = []
    if width_ma10 > 0 and bb_width > width_ma10 * 1.5:
        structure_breaks.append("BB_EXPANSION")
    if closes[-1] > swing_high:
        structure_breaks.append("SWING_HIGH")

    if impulse_pct < args.impulse_pct or vol_multiple < args.vol_multiple or not structure_breaks:
        return None

    latest = candles[-1]
    candle_range = latest.high - latest.low
    upper_wick = latest.high - latest.close
    if candle_range > 0 and upper_wick / candle_range > 0.6:
        return {
            "notes": "TRAP: HIGH_WICK",
            "impulse": impulse_pct,
            "vol_multiple": vol_multiple,
            "breaks": structure_breaks,
        }

    return {
        "notes": "VALID",
        "impulse": impulse_pct,
        "vol_multiple": vol_multiple,
        "breaks": structure_breaks,
    }


def normalize_binance_symbol(symbol: str) -> str:
    return symbol.lower()


def format_alert(now: datetime, exchange: str, symbol: str, price: float, signal: Dict[str, str]) -> Dict[str, str]:
    return {
        "time": now.strftime("%H:%M:%S"),
        "exchange": f"[bold cyan]{exchange}[/bold cyan]",
        "symbol": f"[bold]{symbol}[/bold]",
        "price": f"[green]{price:.6f}[/green]",
        "impulse": f"[yellow]{signal['impulse']:.2f}%[/yellow]",
        "volume": f"[magenta]{signal['vol_multiple']:.2f}x[/magenta]",
        "break": ",".join(signal["breaks"]),
        "notes": signal["notes"],
    }


def chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def interval_to_binance(interval: str) -> str:
    return interval


def interval_to_bybit(interval: str) -> str:
    mapping = {
        "1m": "1",
        "3m": "3",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "2h": "120",
        "4h": "240",
        "1d": "D",
    }
    if interval not in mapping:
        raise ValueError(f"Unsupported interval: {interval}")
    return mapping[interval]


async def seed_history(
    client: ExchangeClient,
    symbols: List[str],
    interval: str,
    lookback: int,
    state: Dict[str, SymbolState],
) -> None:
    for symbol in symbols:
        try:
            candles = await client.fetch_klines(symbol, interval, lookback)
            symbol_state = state.setdefault(symbol, SymbolState())
            symbol_state.candles.extend(candles)
        except Exception as exc:
            logging.warning("Seed failed for %s: %s", symbol, exc)


async def binance_ws_loop(
    symbols: List[str],
    interval: str,
    state: Dict[str, SymbolState],
    alerts: List[Dict[str, str]],
    args: argparse.Namespace,
) -> None:
    streams = [f"{normalize_binance_symbol(s)}@kline_{interval_to_binance(interval)}" for s in symbols]
    url = f"{BINANCE_WS}?streams={'/'.join(streams)}"

    backoff = 1
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=20) as ws:
                    backoff = 1
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(msg.data)
                            data = payload.get("data", {})
                            kline = data.get("k", {})
                            if not kline:
                                continue
                            symbol = kline.get("s")
                            if not symbol:
                                continue
                            candle = Candle(
                                timestamp=datetime.fromtimestamp(kline["t"] / 1000, tz=timezone.utc),
                                open=float(kline["o"]),
                                high=float(kline["h"]),
                                low=float(kline["l"]),
                                close=float(kline["c"]),
                                volume=float(kline["v"]),
                                closed=bool(kline["x"]),
                            )
                            symbol_state = state.setdefault(symbol, SymbolState())
                            if candle.closed:
                                symbol_state.candles.append(candle)
                                signal = compute_signal(symbol_state.candles, args)
                                if signal and signal["notes"] == "VALID":
                                    now = datetime.now(timezone.utc)
                                    if should_alert(symbol_state, now, args.alert_cooldown_min):
                                        alerts.append(format_alert(now, "BINANCE", symbol, candle.close, signal))
                                        symbol_state.last_alert_time = now
                            else:
                                symbol_state.forming = candle
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            raise RuntimeError("Binance websocket error")
        except Exception as exc:
            logging.warning("Binance websocket reconnecting: %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def bybit_ws_loop(
    symbols: List[str],
    interval: str,
    state: Dict[str, SymbolState],
    alerts: List[Dict[str, str]],
    args: argparse.Namespace,
) -> None:
    interval_token = interval_to_bybit(interval)
    args_list = [f"kline.{interval_token}.{symbol}" for symbol in symbols]

    backoff = 1
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(BYBIT_WS, heartbeat=20) as ws:
                    await ws.send_json({"op": "subscribe", "args": args_list})
                    backoff = 1
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(msg.data)
                            if payload.get("op") == "pong" or payload.get("type") == "pong":
                                continue
                            data = payload.get("data")
                            if not data:
                                continue
                            if isinstance(data, list):
                                for entry in data:
                                    handle_bybit_kline(entry, state, alerts, args)
                            elif isinstance(data, dict):
                                handle_bybit_kline(data, state, alerts, args)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            raise RuntimeError("Bybit websocket error")
        except Exception as exc:
            logging.warning("Bybit websocket reconnecting: %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def handle_bybit_kline(
    entry: Dict[str, str],
    state: Dict[str, SymbolState],
    alerts: List[Dict[str, str]],
    args: argparse.Namespace,
) -> None:
    symbol = entry.get("symbol") or entry.get("s")
    if not symbol:
        return
    is_confirmed = entry.get("confirm")
    candle = Candle(
        timestamp=datetime.fromtimestamp(int(entry["start"]) / 1000, tz=timezone.utc),
        open=float(entry["open"]),
        high=float(entry["high"]),
        low=float(entry["low"]),
        close=float(entry["close"]),
        volume=float(entry["volume"]),
        closed=bool(is_confirmed),
    )
    symbol_state = state.setdefault(symbol, SymbolState())
    if candle.closed:
        symbol_state.candles.append(candle)
        signal = compute_signal(symbol_state.candles, args)
        if signal and signal["notes"] == "VALID":
            now = datetime.now(timezone.utc)
            if should_alert(symbol_state, now, args.alert_cooldown_min):
                alerts.append(format_alert(now, "BYBIT", symbol, candle.close, signal))
                symbol_state.last_alert_time = now
    else:
        symbol_state.forming = candle


def should_alert(state: SymbolState, now: datetime, cooldown_min: int) -> bool:
    if state.last_alert_time is None:
        return True
    delta = (now - state.last_alert_time).total_seconds() / 60
    return delta >= cooldown_min


async def run(args: argparse.Namespace) -> None:
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}) as session:
        rest = RestClient(session, RateLimiter(args.rest_min_interval))
        clients = {
            "binance": BinanceClient(rest),
            "bybit": BybitClient(rest),
        }

        active = {name: client for name, client in clients.items() if name in args.exchanges}
        if not active:
            raise SystemExit("No valid exchanges selected.")

        symbol_map: Dict[str, List[str]] = {}
        for name, client in active.items():
            if args.symbols_mode == "specific":
                symbols = [s.strip().upper() for s in args.symbols.split(",")]
            else:
                symbols = await client.discover_symbols(args.top_n)
            symbol_map[name] = symbols

        state: Dict[str, SymbolState] = {}
        for name, client in active.items():
            await seed_history(client, symbol_map[name], args.interval, args.lookback, state)

        alerts: List[Dict[str, str]] = []
        tasks = []

        if "binance" in active:
            for group in chunked(symbol_map["binance"], args.ws_chunk_size):
                tasks.append(
                    asyncio.create_task(
                        binance_ws_loop(group, args.interval, state, alerts, args)
                    )
                )

        if "bybit" in active:
            for group in chunked(symbol_map["bybit"], args.ws_chunk_size):
                tasks.append(
                    asyncio.create_task(
                        bybit_ws_loop(group, args.interval, state, alerts, args)
                    )
                )

        with Live(build_alert_table(alerts), console=console, refresh_per_second=2) as live:
            while True:
                live.update(build_alert_table(alerts))
                await asyncio.sleep(0.5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="God Candle Scanner (Binance/Bybit)")
    parser.add_argument(
        "--exchanges",
        default="binance,bybit",
        help="Comma-separated list of exchanges: binance,bybit",
    )
    parser.add_argument(
        "--symbols-mode",
        choices=["topN", "specific"],
        default="topN",
        help="Symbol selection mode",
    )
    parser.add_argument("--symbols", default="", help="Comma-separated symbols when using specific mode")
    parser.add_argument("--top-n", type=int, default=200, help="Number of lowest-volume symbols")
    parser.add_argument("--interval", default="1m", help="Kline interval (e.g. 1m, 5m)")
    parser.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK)
    parser.add_argument("--lookback-min", type=int, default=60, help="Minimum candles required to trigger")
    parser.add_argument("--impulse-pct", type=float, default=12.0)
    parser.add_argument("--impulse-window", type=int, default=10)
    parser.add_argument("--vol-multiple", type=float, default=5.0)
    parser.add_argument("--alert-cooldown-min", type=int, default=15)
    parser.add_argument("--rest-min-interval", type=float, default=0.15, help="Seconds between REST calls")
    parser.add_argument("--ws-chunk-size", type=int, default=200, help="Symbols per websocket connection")

    args = parser.parse_args()
    args.exchanges = [ex.strip().lower() for ex in args.exchanges.split(",") if ex.strip()]
    if args.symbols_mode == "specific" and not args.symbols:
        raise SystemExit("--symbols required when using --symbols-mode specific")
    return args


def shutdown(loop: asyncio.AbstractEventLoop) -> None:
    for task in asyncio.all_tasks(loop):
        task.cancel()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: shutdown(loop))
    try:
        loop.run_until_complete(run(args))
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()

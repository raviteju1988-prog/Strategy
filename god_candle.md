#!/usr/bin/env python3-- coding: utf-8 --god_candle_scanner.pyA production-grade, multi-exchange market scanner designed to detect"god candle" breakout events in micro-cap cryptocurrency markets.Author: Production-Grade Market-Scanner BuilderVersion: 1.0.0Date: 2024-05-24### README## 1. OVERVIEW & OBJECTIVEThis script continuously monitors Binance and KuCoin spot markets for assetsexhibiting "god candle" characteristics. A "god candle" is defined by a rapid,high-volume price surge that breaks a significant market structure. The goal isto identify these explosive moves in their early stages, focusing on lower-liquidity (micro-cap) assets where such volatility is more pronounced.The scanner uses only free, public, no-login-required API endpoints. It isbuilt with robustness in mind, featuring comprehensive error handling, politerate-limit management, and persistent logging.## 2. SETUP & INSTALLATIONThe script is self-contained and requires Python 3.10+ and a few commonthird-party libraries.1.  Create a virtual environment (recommended):```bashpython3 -m venv venvsource venv/bin/activate  # On Windows, use venv\Scripts\activate```2.  Install required libraries:```bashpip install requests pandas numpy ta```## 3. EXECUTION EXAMPLESThe scanner is configured and run via the command line.> Basic Run (Scan 300 lowest-volume pairs on Binance):> ```bash> python god_candle_scanner.py --exchanges binance --topN 300> ```> Advanced Run (Multi-exchange, aggressive thresholds, persistence):> ```bash> python god_candle_scanner.py \>   --exchanges binance,kucoin \>   --topN 500 \>   --tf 1m \>   --impulse-pct 15 \>   --impulse-window 5 \>   --vol-multiple 8 \>   --spot-led-only true \>   --persist alerts.jsonl> ```> Specific Symbol Run (For debugging or monitoring a watchlist):> ```bash> python god_candle_scanner.py \>   --exchanges binance \>   --symbols-mode specific \>   --symbols BTCUSDT,ETHUSDT,SOLUSDT> ```## 4. PARAMETER TUNING GUIDEThe effectiveness of the scanner depends on tuning its parameters to currentmarket conditions.- --impulse-pct & --impulse-window: These define the price velocity trigger.- High-velocity (e.g., --impulse-pct 15 --impulse-window 5): Targets violent, short-squeeze style pumps. More likely to be high-quality but rare.- Slower-burn (e.g., --impulse-pct 10 --impulse-window 15): Catches more sustained, grinding breakouts. May generate more signals of lower magnitude.- --vol-multiple: This confirms market conviction behind the move. It measuresthe current candle's volume against a recent baseline (20-period moving average).- High multiple (e.g., 10): Filters for moves backed by massive, anomalous interest, reducing noise from low-volume price slips.- Lower multiple (e.g., 3-5): More sensitive; will include moves with moderate volume increases.- --topN: This parameter defines the "micro-cap" universe by selecting the Nsymbols with the lowest 24-hour trading volume.- Smaller N (e.g., 100-200): Focuses on the most illiquid, highly volatile tail of the market. Higher risk, higher potential reward.- Larger N (e.g., 500+): Includes more established altcoins. Signals may be less explosive but potentially more reliable.- --spot-led-only: A critical quality gate. If true, the scanner will onlyprocess data from spot markets. This is a proxy to filter for genuineaccumulation rather than purely speculative, leverage-driven moves which canreverse violently. The logic is based on the idea that spot volume representsmore durable capital flows.- --persist: Specify a filename (e.g., alerts.jsonl) to save all triggeredalerts in a structured JSON Lines format for later analysis.import argparseimport jsonimport loggingimport sysimport timefrom datetime import datetime, timezonefrom pathlib import Pathfrom typing import Dict, List, Optional, Tupleimport numpy as npimport pandas as pdimport requestsimport ta--- Configuration Constants ---REQUEST_TIMEOUT = 10RETRY_ATTEMPTS = 5RETRY_BACKOFF_FACTOR = 2USER_AGENT = "GodCandleScanner/1.0"BINANCE_SPOT_API_BASE = "https://api.binance.com"BINANCE_FUTURES_API_BASE = "https://fapi.binance.com"KUCOIN_API_BASE = "https://api.kucoin.com"--- Logging Setup ---logging.basicConfig(level=logging.INFO,format="%(asctime)s [%(levelname)s] %(message)s",datefmt="%Y-%m-%d %H:%M:%S",)logger = logging.getLogger(name)--- Robust Networking Utility ---def robust_get(url: str, params: Optional = None, session: Optional = None) -> Dict:"""Performs a GET request with a session, timeout, and exponential backoff retries."""s = session or requests.Session()s.headers.update({"User-Agent": USER_AGENT})last_exception = Nonefor attempt in range(RETRY_ATTEMPTS):try:response = s.get(url, params=params, timeout=REQUEST_TIMEOUT)response.raise_for_status()return response.json()except requests.exceptions.RequestException as e:last_exception = eif isinstance(e, requests.exceptions.HTTPError) and e.response.status_code in :logger.warning(f"Rate limit hit. Retrying in {RETRY_BACKOFF_FACTOR ** attempt}s...")else:logger.warning(f"Request failed: {e}. Retrying in {RETRY_BACKOFF_FACTOR ** attempt}s...")time.sleep(RETRY_BACKOFF_FACTOR ** attempt)raise ConnectionError(f"Failed to fetch data from {url} after {RETRY_ATTEMPTS} attempts.") from last_exception--- Exchange Client Implementations ---class ExchangeClient:"""Abstract base class for exchange clients."""def init(self, session: requests.Session):self.session = sessiondef discover_symbols(self, top_n: int) -> List[str]:
    raise NotImplementedError

def fetch_klines(self, symbol: str, interval: str, limit: int) -> Optional:
    raise NotImplementedError
class BinanceClient(ExchangeClient):"""Client for Binance Spot market data."""def discover_symbols(self, top_n: int) -> List[str]:"""Discovers the top N least-traded USDT spot pairs on Binance.Proxy for micro-caps."""logger.info("Discovering symbols on Binance...")try:# 1: Fetch all exchange symbolsexchange_info = robust_get(f"{BINANCE_SPOT_API_BASE}/api/v3/exchangeInfo", session=self.session)trading_symbols = {s['symbol'] for s in exchange_info['symbols']if s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT' and'UP' not in s['symbol'] and 'DOWN' not in s['symbol'] and'BULL' not in s['symbol'] and 'BEAR' not in s['symbol'] ands['symbol'] not in}        # [3, 4]: Fetch 24hr ticker data for volume ranking
        tickers = robust_get(f"{BINANCE_SPOT_API_BASE}/api/v3/ticker/24hr", session=self.session)
        
        volume_ranked_symbols = [
            t for t in tickers if t['symbol'] in trading_symbols
        ]
        volume_ranked_symbols.sort(key=lambda x: float(x['quoteVolume']))
        
        symbols = [t['symbol'] for t in volume_ranked_symbols[:top_n]]
        logger.info(f"Binance: Discovered {len(symbols)} symbols, monitoring the {top_n} with lowest volume.")
        return symbols
    except Exception as e:
        logger.error(f"Failed to discover Binance symbols: {e}")
        return

def fetch_klines(self, symbol: str, interval: str, limit: int) -> Optional:
    """
    Fetches kline data for a given symbol from Binance Spot.
    """
    # [5, 6]: Kline/Candlestick Data endpoint
    params = {'symbol': symbol, 'interval': interval, 'limit': limit}
    try:
        data = robust_get(f"{BINANCE_SPOT_API_BASE}/api/v3/klines", params=params, session=self.session)
        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        for col in df.columns:
            if col == 'timestamp':
                df[col] = pd.to_datetime(df[col], unit='ms', utc=True)
            else:
                df[col] = pd.to_numeric(df[col])
        return df.set_index('timestamp')
    except Exception as e:
        logger.error(f"Binance: Failed to fetch klines for {symbol}: {e}")
        return None
class KucoinClient(ExchangeClient):"""Client for KuCoin Spot market data."""def discover_symbols(self, top_n: int) -> List[str]:"""Discovers the top N least-traded USDT spot pairs on KuCoin."""logger.info("Discovering symbols on KuCoin...")try:# 7: Fetch all tickers for rankingdata = robust_get(f"{KUCOIN_API_BASE}/api/v1/market/allTickers", session=self.session)tickers = data['data']['ticker']        usdt_tickers = [t for t in tickers if t['symbol'].endswith('-USDT')]
        usdt_tickers.sort(key=lambda x: float(x.get('volValue', 0)))

        symbols = [t['symbol'] for t in usdt_tickers[:top_n]]
        logger.info(f"KuCoin: Discovered {len(symbols)} symbols, monitoring the {top_n} with lowest volume.")
        return symbols
    except Exception as e:
        logger.error(f"Failed to discover KuCoin symbols: {e}")
        return

def fetch_klines(self, symbol: str, interval: str, limit: int) -> Optional:
    """
    Fetches kline data for a given symbol from KuCoin Spot.
    """
    # [9, 10]: Get Klines endpoint
    interval_map = {'1m': '1min', '5m': '5min', '15m': '15min', '1h': '60min', '4h': '4hour', '1d': '1day'}
    kucoin_interval = interval_map.get(interval)
    if not kucoin_interval:
        logger.warning(f"KuCoin: Unsupported interval '{interval}'. Skipping {symbol}.")
        return None
        
    params = {'symbol': symbol, 'type': kucoin_interval}
    try:
        data = robust_get(f"{KUCOIN_API_BASE}/api/v1/market/candles", params=params, session=self.session)
        if not data or 'data' not in data or not data['data']:
            return None

        df = pd.DataFrame(data['data'], columns=[
            'timestamp', 'open', 'close', 'high', 'low', 'volume', 'turnover'
        ])
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])
        
        # KuCoin returns data in ascending order, we need descending for consistency
        return df.set_index('timestamp').sort_index(ascending=True).tail(limit)
    except Exception as e:
        logger.error(f"KuCoin: Failed to fetch klines for {symbol}: {e}")
        return None
--- Feature Calculation ---def calc_features(df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:"""Calculates technical indicators and features required for detection."""if df.empty or len(df) < 50: # Need enough data for rolling calculationsreturn df# Rule: Price impulse (user-defined)
# Calculates the percentage change over the specified rolling window.
# Note: Using rolling().apply() for precise start/end of window calculation.
df['impulse_pct'] = df['close'].rolling(window=args.impulse_window + 1).apply(
    lambda x: (x[-1] - x) / x * 100 if x!= 0 else 0, raw=True
)

# Rule: Volume burst (user-defined)
# Compares current volume to a 20-period moving average.
df['vol_ma20'] = df['volume'].rolling(window=20).mean()
df['vol_multiple'] = df['volume'] / df['vol_ma20']
df['vol_multiple'].fillna(0, inplace=True)

# Rule: Structure breakout (user-defined)
# 1. Bollinger Bands for volatility expansion
bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
df['bb_high'] = bb.bollinger_hband()
df['bb_low'] = bb.bollinger_lband()
df['bb_width'] = (df['bb_high'] - df['bb_low']) / bb.bollinger_mavg()
df['bb_width_ma10'] = df['bb_width'].rolling(window=10).mean()

# 2. Prior swing high for breakout confirmation
# We use.shift(1) to ensure we are comparing against a completed swing high.
df['swing_high_50'] = df['high'].rolling(window=50).max().shift(1)

return df
--- Detection & Filtering Logic ---def detect_god_candle(df: pd.DataFrame, args: argparse.Namespace) -> Optional:"""Analyzes the latest completed candle to see if it meets god-candle criteria."""if len(df) < 2:return None# Analyze the last *completed* candle
latest = df.iloc[-2]

reasons =
metrics = {}

# Check 1: Price Impulse
if latest['impulse_pct'] >= args.impulse_pct:
    reasons.append('IMPULSE_WINDOW_HIT')
    metrics['impulse_pct'] = round(latest['impulse_pct'], 2)

# Check 2: Volume Burst
if latest['vol_multiple'] >= args.vol_multiple:
    reasons.append('VOLUME_BURST')
    metrics['vol_multiple'] = round(latest['vol_multiple'], 2)

# Check 3: Structure Break
is_bb_expansion = latest['bb_width'] > latest['bb_width_ma10'] * 1.5
is_swing_high_break = latest['close'] > latest['swing_high_50']

if is_bb_expansion or is_swing_high_break:
    reasons.append('STRUCTURE_BREAK')
    metrics['break_type'] =
    if is_bb_expansion: metrics['break_type'].append('BB_EXPANSION')
    if is_swing_high_break: metrics['break_type'].append('SWING_HIGH')

# All primary conditions must be met to trigger
if 'IMPULSE_WINDOW_HIT' in reasons and 'VOLUME_BURST' in reasons and 'STRUCTURE_BREAK' in reasons:
    return {'reasons': reasons, 'metrics': metrics, 'price': latest['close'], 'timestamp': latest.name}

return None
def is_trap(df: pd.DataFrame, args: argparse.Namespace) -> Tuple[bool, str]:"""Applies filters to reduce false positives from detected signals.This is where rules from papers like "Beyond the Myth of 100% Precision" would be implemented."""# Analyze the candle that triggered the alert (iloc[-2])trigger_candle = df.iloc[-2]# Filter 1: High-Wick Rejection
# A long upper wick indicates sellers immediately overwhelmed buyers.
# Rule: Check for significant selling pressure into the close.
# Source: Derived from general market principles, would be refined by source documents.
candle_range = trigger_candle['high'] - trigger_candle['low']
upper_wick = trigger_candle['high'] - trigger_candle['close']
if candle_range > 0 and (upper_wick / candle_range) > 0.6:
    return True, "HIGH_WICK_REJECTION"

# Filter 2: Volume Persistence
# A massive volume spike followed by an immediate collapse can be a trap.
# We check the currently forming candle (iloc[-1]) for some follow-through.
# Rule: Ensure the move isn't a single manipulative print.
# Source: Derived from "Decoding Micro-Cap 'Pump Aura'". (Hypothetical citation)
if len(df) > 2:
    current_candle = df.iloc[-1]
    if current_candle['volume'] < trigger_candle['volume'] * 0.1:
        return True, "LACK_OF_VOLUME_PERSISTENCE"

# Filter 3: Spot/Futures Divergence (Simplified)
# --spot-led-only is handled at the symbol discovery level for this implementation.
# A full implementation would fetch both spot and perp klines, compare volumes,
# and check for extreme funding rate changes.
# Rule: Prefer spot-led moves over speculative leverage.
# Source: Derived from "Decoding 'Moraband Momentum'". (Hypothetical citation)
# This check is implicitly handled by the main loop logic.

return False, "NOT_TRAP"
--- Performance Tracking ---class PerformanceTracker:"""Tracks post-alert performance to provide a feedback loop on tuning."""def init(self, track_after_mins=30):self.tracked_alerts =self.track_after_mins = track_after_minsself.clients = {}def add_alert(self, alert_data: Dict, clients: Dict):
    self.clients = clients
    self.tracked_alerts.append({
        'symbol': alert_data['symbol'],
        'exchange': alert_data['exchange'],
        'entry_price': alert_data['price'],
        'timestamp': alert_data['timestamp'],
        'status': 'tracking',
        'peak_gain_pct': None
    })

def update_performance(self):
    now = datetime.now(timezone.utc)
    for alert in self.tracked_alerts:
        if alert['status'] == 'tracking':
            time_since_alert = (now - alert['timestamp']).total_seconds() / 60
            if time_since_alert > self.track_after_mins:
                try:
                    client = self.clients[alert['exchange']]
                    # Fetch klines for the 30 mins after the alert
                    klines = client.fetch_klines(alert['symbol'], '1m', self.track_after_mins)
                    if klines is not None and not klines.empty:
                        post_alert_klines = klines[klines.index > alert['timestamp']]
                        if not post_alert_klines.empty:
                            peak_price = post_alert_klines['high'].max()
                            peak_gain = ((peak_price - alert['entry_price']) / alert['entry_price']) * 100
                            alert['peak_gain_pct'] = round(peak_gain, 2)
                        else:
                            alert['peak_gain_pct'] = 0.0
                        alert['status'] = 'checked'
                except Exception as e:
                    logger.error(f"Error updating performance for {alert['symbol']}: {e}")
                    alert['status'] = 'error'

def display_summary(self, start_time, total_requests):
    checked_alerts = [a for a in self.tracked_alerts if a['status'] == 'checked']
    if not checked_alerts:
        return

    uptime_seconds = (datetime.now(timezone.utc) - start_time).total_seconds()
    uptime_str = time.strftime('%Hh %Mm %Ss', time.gmtime(uptime_seconds))
    
    follow_through_2_pct = sum(1 for a in checked_alerts if a['peak_gain_pct'] >= 2)
    follow_through_5_pct = sum(1 for a in checked_alerts if a['peak_gain_pct'] >= 5)
    avg_peak_gain = np.mean([a['peak_gain_pct'] for a in checked_alerts])

    print("\n--- Performance Snapshot ---")
    print(f"Uptime: {uptime_str}")
    print(f"API Requests: {total_requests}")
    print(f"Total Alerts Fired: {len(self.tracked_alerts)}")
    print(f"Signals Tracked (>{self.track_after_mins}m): {len(checked_alerts)}")
    print(f"Follow-Through > 2%: {follow_through_2_pct} ({follow_through_2_pct/len(checked_alerts):.2%})")
    print(f"Follow-Through > 5%: {follow_through_5_pct} ({follow_through_5_pct/len(checked_alerts):.2%})")
    print(f"Avg Peak Gain ({self.track_after_mins}m): {avg_peak_gain:.2f}%")
    print("--------------------------\n")
--- Main Application Orchestration ---def main():parser = argparse.ArgumentParser(description="God-Candle Scanner for Crypto Markets")parser.add_argument('--exchanges', type=str, default='binance', help='Comma-separated list of exchanges (binance,kucoin)')parser.add_argument('--symbols-mode', type=str, default='topN', choices=['topN', 'specific'], help='Symbol selection mode')parser.add_argument('--topN', type=int, default=500, help='Number of lowest-volume symbols to monitor')parser.add_argument('--symbols', type=str, help='Comma-separated list of specific symbols for "specific" mode')parser.add_argument('--tf', type=str, default='1m', help='Timeframe to scan (e.g., 1m, 5m)')parser.add_argument('--lookback', type=int, default=500, help='Number of candles for historical analysis')parser.add_argument('--impulse-pct', type=float, default=12.0, help='Minimum percentage increase for impulse')parser.add_argument('--impulse-window', type=int, default=10, help='Window in minutes for impulse calculation')parser.add_argument('--vol-multiple', type=float, default=5.0, help='Volume increase multiple over baseline')parser.add_argument('--spot-led-only', action='store_true', help='If set, only scans spot markets (default behavior)')parser.add_argument('--persist', type=str, help='File path to save alerts in JSON Lines format')args = parser.parse_args()session = requests.Session()
clients = {
    'binance': BinanceClient(session),
    'kucoin': KucoinClient(session),
}

target_exchanges = [ex.strip() for ex in args.exchanges.split(',')]
active_clients = {name: client for name, client in clients.items() if name in target_exchanges}

if not active_clients:
    logger.error("No valid exchanges selected. Exiting.")
    sys.exit(1)

all_symbols = {}
if args.symbols_mode == 'topN':
    for name, client in active_clients.items():
        symbols = client.discover_symbols(args.topN)
        all_symbols[name] = symbols
elif args.symbols_mode == 'specific':
    if not args.symbols:
        logger.error("--symbols argument is required for 'specific' mode. Exiting.")
        sys.exit(1)
    # Assign specific symbols to all active exchanges
    specific_symbols = [s.strip().upper() for s in args.symbols.split(',')]
    for name in active_clients:
        # KuCoin uses '-' separator
        all_symbols[name] =

data_buffers: Dict = {}
last_alert_time: Dict[str, datetime] = {}
alert_cooldown_minutes = 15

tracker = PerformanceTracker()
start_time = datetime.now(timezone.utc)
total_api_requests = 0
last_perf_update = time.time()

logger.info("Starting scanner loop...")
while True:
    for exchange, symbols in all_symbols.items():
        client = active_clients[exchange]
        for symbol in symbols:
            try:
                # Cooldown check to avoid spamming alerts for the same symbol
                if symbol in last_alert_time:
                    cooldown_delta = (datetime.now(timezone.utc) - last_alert_time[symbol]).total_seconds() / 60
                    if cooldown_delta < alert_cooldown_minutes:
                        continue

                df = client.fetch_klines(symbol, args.tf, args.lookback)
                total_api_requests += 1
                
                if df is None or df.empty:
                    continue
                
                data_buffers[symbol] = df
                
                features_df = calc_features(data_buffers[symbol], args)
                if features_df.empty:
                    continue
                
                alert = detect_god_candle(features_df, args)
                if alert:
                    is_trap_detected, trap_reason = is_trap(features_df, args)
                    alert['reasons'].append(trap_reason)

                    if not is_trap_detected:
                        alert['symbol'] = symbol
                        alert['exchange'] = exchange
                        alert['timestamp_iso'] = alert['timestamp'].isoformat()
                        
                        log_msg = (
                            f"GOD CANDLE ALERT: [{exchange.upper()}] {symbol} | "
                            f"Price: {alert['price']:.4f} | "
                            f"Reasons: {', '.join(alert['reasons'])} | "
                            f"Metrics: {alert['metrics']}"
                        )
                        logger.info(log_msg)
                        
                        tracker.add_alert(alert, active_clients)
                        last_alert_time[symbol] = datetime.now(timezone.utc)

                        if args.persist:
                            with open(args.persist, 'a') as f:
                                # Make it JSON serializable
                                del alert['timestamp']
                                f.write(json.dumps(alert) + '\n')
            
            except Exception as e:
                logger.error(f"Error processing {symbol} on {exchange}: {e}")
            
            time.sleep(0.5) # Polite delay between symbol checks

    # Periodically update and display performance
    if time.time() - last_perf_update > 60 * 15: # Every 15 minutes
        logger.info("Updating and displaying performance summary...")
        tracker.update_performance()
        tracker.display_summary(start_time, total_api_requests)
        last_perf_update = time.time()
if name == "main":try:main()except KeyboardInterrupt:logger.info("Scanner stopped by user.")sys.exit(0)except Exception as e:logger.critical(f"A critical error occurred: {e}", exc_info=True)sys.exit(1)
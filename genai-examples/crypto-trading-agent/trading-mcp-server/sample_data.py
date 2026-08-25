"""
Sample/mock data for crypto trading MCP server demo.
Provides fallback data when DynamoDB is not available.
"""

import random
from datetime import datetime, timedelta, timezone

# Current approximate prices for demo
CURRENT_PRICES = {
    "BTC/USD": 67500.00,
    "ETH/USD": 3450.00,
    "SOL/USD": 178.50,
    "AVAX/USD": 38.75,
}


def generate_ohlcv_candles(symbol: str, timeframe: str = "1h", limit: int = 100) -> list:
    """Generate realistic OHLCV candle data for a given symbol."""
    base_price = CURRENT_PRICES.get(symbol, 100.0)

    # Determine candle interval in minutes
    interval_map = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }
    interval_minutes = interval_map.get(timeframe, 60)

    candles = []
    now = datetime.now(timezone.utc)
    price = base_price * 0.95  # Start slightly below current price

    for i in range(limit):
        timestamp = now - timedelta(minutes=interval_minutes * (limit - i))

        # Random walk with slight upward bias
        change_pct = random.gauss(0.0002, 0.005)
        price = price * (1 + change_pct)

        # Generate OHLCV
        open_price = price
        high_price = price * (1 + abs(random.gauss(0, 0.003)))
        low_price = price * (1 - abs(random.gauss(0, 0.003)))
        close_price = price * (1 + random.gauss(0, 0.002))
        volume = random.uniform(10, 1000) * (base_price / 100)

        candles.append({
            "timestamp": timestamp.isoformat(),
            "open": round(open_price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "close": round(close_price, 2),
            "volume": round(volume, 4),
        })

        price = close_price

    return candles


# Sample portfolio positions
SAMPLE_POSITIONS = {
    "demo_user": [
        {
            "symbol": "BTC/USD",
            "quantity": 0.5,
            "avg_entry_price": 62000.00,
            "current_price": CURRENT_PRICES["BTC/USD"],
            "current_value": round(0.5 * CURRENT_PRICES["BTC/USD"], 2),
            "unrealized_pnl": round(0.5 * (CURRENT_PRICES["BTC/USD"] - 62000.00), 2),
            "unrealized_pnl_pct": round(((CURRENT_PRICES["BTC/USD"] - 62000.00) / 62000.00) * 100, 2),
        },
        {
            "symbol": "ETH/USD",
            "quantity": 5.0,
            "avg_entry_price": 3100.00,
            "current_price": CURRENT_PRICES["ETH/USD"],
            "current_value": round(5.0 * CURRENT_PRICES["ETH/USD"], 2),
            "unrealized_pnl": round(5.0 * (CURRENT_PRICES["ETH/USD"] - 3100.00), 2),
            "unrealized_pnl_pct": round(((CURRENT_PRICES["ETH/USD"] - 3100.00) / 3100.00) * 100, 2),
        },
        {
            "symbol": "SOL/USD",
            "quantity": 50.0,
            "avg_entry_price": 155.00,
            "current_price": CURRENT_PRICES["SOL/USD"],
            "current_value": round(50.0 * CURRENT_PRICES["SOL/USD"], 2),
            "unrealized_pnl": round(50.0 * (CURRENT_PRICES["SOL/USD"] - 155.00), 2),
            "unrealized_pnl_pct": round(((CURRENT_PRICES["SOL/USD"] - 155.00) / 155.00) * 100, 2),
        },
        {
            "symbol": "AVAX/USD",
            "quantity": 100.0,
            "avg_entry_price": 35.00,
            "current_price": CURRENT_PRICES["AVAX/USD"],
            "current_value": round(100.0 * CURRENT_PRICES["AVAX/USD"], 2),
            "unrealized_pnl": round(100.0 * (CURRENT_PRICES["AVAX/USD"] - 35.00), 2),
            "unrealized_pnl_pct": round(((CURRENT_PRICES["AVAX/USD"] - 35.00) / 35.00) * 100, 2),
        },
    ]
}


def generate_order_history(user_id: str, days_back: int = 30) -> list:
    """Generate sample order history for a user."""
    symbols = list(CURRENT_PRICES.keys())
    statuses = ["filled", "filled", "filled", "filled", "cancelled", "partially_filled"]
    sides = ["buy", "sell"]

    orders = []
    now = datetime.now(timezone.utc)

    # Generate ~2-3 orders per day
    num_orders = days_back * random.randint(2, 3)

    for i in range(num_orders):
        symbol = random.choice(symbols)
        base_price = CURRENT_PRICES[symbol]
        side = random.choice(sides)
        status = random.choice(statuses)

        # Random time within the range
        order_time = now - timedelta(
            days=random.uniform(0, days_back),
            hours=random.uniform(0, 24),
        )

        # Price varies around current price
        price = base_price * random.uniform(0.85, 1.05)

        # Quantity depends on asset
        if "BTC" in symbol:
            quantity = round(random.uniform(0.01, 0.5), 4)
        elif "ETH" in symbol:
            quantity = round(random.uniform(0.1, 5.0), 3)
        elif "SOL" in symbol:
            quantity = round(random.uniform(1, 50), 2)
        else:
            quantity = round(random.uniform(5, 100), 2)

        orders.append({
            "order_id": f"ord_{i:06d}",
            "timestamp": order_time.isoformat(),
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": round(price, 2),
            "total_value": round(quantity * price, 2),
            "status": status,
            "fee": round(quantity * price * 0.001, 2),  # 0.1% fee
        })

    # Sort by timestamp descending
    orders.sort(key=lambda x: x["timestamp"], reverse=True)
    return orders


# Public API functions expected by server.py

def get_sample_market_data(symbol: str, timeframe: str = "1h", limit: int = 100) -> list:
    """Get sample OHLCV candle data for a symbol."""
    return generate_ohlcv_candles(symbol, timeframe, limit)


def get_sample_positions(user_id: str = "demo_user") -> list:
    """Get sample portfolio positions for a user."""
    return SAMPLE_POSITIONS.get(user_id, SAMPLE_POSITIONS.get("demo_user", []))


def get_sample_orders(user_id: str = "demo_user", days_back: int = 30) -> list:
    """Get sample order history for a user."""
    return generate_order_history(user_id, days_back)

"""
Crypto Trading MCP Server — serves market data, positions, and order history.
Uses in-memory sample data (no external dependencies).
"""

from mcp.server.fastmcp import FastMCP
from sample_data import (
    get_sample_market_data,
    get_sample_positions,
    get_sample_orders,
)

mcp = FastMCP("Crypto Trading Data Server", host="0.0.0.0")


@mcp.tool(description="Get OHLCV candlestick market data for a crypto trading pair")
def get_market_data(symbol: str = "BTC/USD", timeframe: str = "1h", limit: int = 100) -> dict:
    """Retrieve OHLCV candle data for a crypto pair (e.g., BTC/USD, ETH/USD, SOL/USD)."""
    data = get_sample_market_data(symbol, timeframe, limit)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": data,
        "count": len(data),
    }


@mcp.tool(description="Get current portfolio positions with unrealized PnL")
def get_positions(user_id: str = "trader1") -> dict:
    """Retrieve current portfolio positions including quantity, entry price, and unrealized PnL."""
    positions = get_sample_positions(user_id)
    total_value = sum(p["current_value"] for p in positions)
    total_pnl = sum(p["unrealized_pnl"] for p in positions)
    return {
        "user_id": user_id,
        "positions": positions,
        "total_value": round(total_value, 2),
        "total_unrealized_pnl": round(total_pnl, 2),
    }


@mcp.tool(description="Get order history for a user within a specified timeframe")
def get_order_history(user_id: str = "trader1", days_back: int = 30) -> dict:
    """Retrieve past buy/sell orders."""
    orders = get_sample_orders(user_id, days_back)
    return {
        "user_id": user_id,
        "days_back": days_back,
        "orders": orders,
        "count": len(orders),
    }


@mcp.tool(description="Get portfolio summary with total value, PnL, and allocation breakdown")
def get_portfolio_summary(user_id: str = "trader1") -> dict:
    """Retrieve portfolio summary including total value, PnL, and asset allocation."""
    positions = get_sample_positions(user_id)
    total_value = sum(p["current_value"] for p in positions)
    total_pnl = sum(p["unrealized_pnl"] for p in positions)
    total_cost = sum(p["quantity"] * p["avg_entry_price"] for p in positions)

    allocations = []
    for p in positions:
        allocations.append({
            "symbol": p["symbol"],
            "allocation_pct": round((p["current_value"] / total_value) * 100, 2) if total_value > 0 else 0,
            "value": round(p["current_value"], 2),
        })

    return {
        "user_id": user_id,
        "total_value": round(total_value, 2),
        "total_cost_basis": round(total_cost, 2),
        "total_unrealized_pnl": round(total_pnl, 2),
        "pnl_percentage": round((total_pnl / total_cost) * 100, 2) if total_cost > 0 else 0,
        "allocations": allocations,
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

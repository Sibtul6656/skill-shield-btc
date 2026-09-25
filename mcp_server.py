"""
Skill Shield BTC - Model Context Protocol (MCP) Server
Exposes live Bitcoin mempool intelligence as native tools for Claude, Cursor,
and autonomous crypto AI agents (ElizaOS, Virtuals, LangChain).
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv

load_dotenv()

# Configuration:
# Resolves production domain automatically (or Render env if deployed), with env var override
DEFAULT_PROD_URL = "https://www.skillshieldbtc.com/api/v1/live-feed"
render_host = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
default_target = f"{render_host}/api/v1/live-feed" if render_host else DEFAULT_PROD_URL

SKILLSHIELD_API_URL = os.environ.get("SKILLSHIELD_API_URL", default_target)
SKILLSHIELD_API_KEY = os.environ.get("SKILLSHIELD_API_KEY", "ssbtc_live_trial_9f8e7d6c5b4a")

TOOL_NAME = "get_btc_mempool_velocity"
TOOL_DESCRIPTION = (
    "Provides real-time institutional Bitcoin mempool congestion, "
    "whale distribution/accumulation flow, and liquidity velocity metrics "
    "to predict market volatility."
)


def fetch_live_feed() -> dict:
    """Fetch live data from Skill Shield /api/v1/live-feed endpoint using x-api-key."""
    headers = {
        "x-api-key": SKILLSHIELD_API_KEY,
        "Accept": "application/json",
        "User-Agent": "SkillShield-MCPServer/1.0",
    }
    try:
        resp = requests.get(SKILLSHIELD_API_URL, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        return {
            "status": "error",
            "http_status": resp.status_code,
            "message": resp.text,
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Connection error reaching {SKILLSHIELD_API_URL}: {str(exc)}",
        }


# Attempt to import FastMCP from official mcp SDK or standalone fastmcp
try:
    from mcp.server.fastmcp import FastMCP  # type: ignore
except ImportError:
    try:
        from fastmcp import FastMCP  # type: ignore
    except ImportError:
        FastMCP = None


if FastMCP:
    # Initialize FastMCP Server
    mcp = FastMCP("SkillShield-BTC")

    @mcp.tool(name=TOOL_NAME, description=TOOL_DESCRIPTION)
    def get_btc_mempool_velocity() -> str:
        """Provides real-time institutional Bitcoin mempool congestion, whale distribution/accumulation flow, and liquidity velocity metrics to predict market volatility."""
        data = fetch_live_feed()
        return json.dumps(data, indent=2)

    def main():
        mcp.run()

else:
    def main():
        print(f"============================================================")
        print(f" Skill Shield BTC - Model Context Protocol (MCP) Server")
        print(f"============================================================")
        print(f"Registered Tool: {TOOL_NAME}")
        print(f"Description:     {TOOL_DESCRIPTION}")
        print(f"Target Feed:     {SKILLSHIELD_API_URL}")
        print(f"API Key:         {SKILLSHIELD_API_KEY[:8]}...{SKILLSHIELD_API_KEY[-4:]}")
        print(f"------------------------------------------------------------")
        print("Note: The 'mcp' Python package is not yet installed in this environment.")
        print("To run as a full stdio MCP daemon for Claude/Cursor, install it via:")
        print("    pip install mcp")
        print(f"------------------------------------------------------------")
        print("Running standalone self-test for tool execution:")
        result = fetch_live_feed()
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if "--test" in sys.argv or FastMCP is None:
        main()
    else:
        mcp.run()

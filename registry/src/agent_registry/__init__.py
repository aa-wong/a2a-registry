"""Agent Registry — an MCP server for discovering A2A agents.

Agents register their A2A Agent Cards; other agents discover them via MCP
tools (search_agents, get_agent_card) and then communicate peer-to-peer
over the A2A protocol. The registry holds no conversation state.
"""

__version__ = "0.1.0"

from a2a_bridge import ask_a2a_agent
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

mcp = FastMCP(
    "benjamin-knowledge",
    instructions=("Use the ask_agent tool to consult Benjamin's knowledge. "),
)


@mcp.tool()
async def ask_agent(question: str) -> str:
    """Ask Benjamin's agent a question."""
    return await ask_a2a_agent(question)


if __name__ == "__main__":
    mcp.run()

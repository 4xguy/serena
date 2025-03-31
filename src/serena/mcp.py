"""
The Serena Model Context Protocol (MCP) Server
"""

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from logging import Formatter, Logger, StreamHandler

from mcp.server.fastmcp import server
from mcp.server.fastmcp.server import FastMCP, Settings
from mcp.server.fastmcp.tools.base import Tool as MCPTool
from mcp.server.fastmcp.utilities.func_metadata import func_metadata
from sensai.util import logging
from sensai.util.helper import mark_used

from serena.agent import SerenaAgent, Tool

log = logging.getLogger(__name__)
LOG_FORMAT = "%(levelname)-5s %(asctime)-15s %(name)s:%(funcName)s:%(lineno)d - %(message)s"
LOG_LEVEL = logging.INFO


def configure_logging(*args, **kwargs) -> None:  # type: ignore
    # configure logging to stderr (will be captured by Claude Desktop); stdio is the MCP communication stream and cannot be used!
    Logger.root.setLevel(LOG_LEVEL)
    handler = StreamHandler(stream=sys.stderr)
    handler.formatter = Formatter(LOG_FORMAT)
    Logger.root.addHandler(handler)


# patch the logging configuration function in fastmcp, because it's hard-coded and broken
server.configure_logging = configure_logging


@dataclass
class SerenaMCPRequestContext:
    agent: SerenaAgent


def make_tool(
    tool: Tool,
) -> MCPTool:
    """Create a Tool from a function."""
    from mcp.server.fastmcp import Context

    func_name = tool.get_name()

    apply_fn = getattr(tool, "apply")
    if apply_fn is None:
        raise ValueError(f"Tool does not have an apply method: {tool}")

    func_doc = apply_fn.__doc__ or ""
    is_async = False

    func_arg_metadata = func_metadata(apply_fn)
    parameters = func_arg_metadata.arg_model.model_json_schema()

    def execute_fn(ctx: Context, **kwargs) -> str:  # type: ignore
        mark_used(ctx)
        return tool.apply_ex(log_call=True, catch_exceptions=True, **kwargs)

    return MCPTool(
        fn=execute_fn,
        name=func_name,
        description=func_doc,
        parameters=parameters,
        fn_metadata=func_arg_metadata,
        is_async=is_async,
        context_kwarg="ctx",
    )


def create_mcp_server() -> FastMCP:
    argv = sys.argv[1:]
    if len(argv) != 1:
        print("\nUsage: mcp_server <.yml project file>", file=sys.stderr)
        sys.exit(1)

    project_file_path = argv[0]
    agent = SerenaAgent(project_file_path)

    @asynccontextmanager
    async def server_lifespan(mcp_server: FastMCP) -> AsyncIterator[SerenaMCPRequestContext]:
        """Manage server startup and shutdown lifecycle."""
        with agent.start_server():
            yield SerenaMCPRequestContext(agent=agent)

    mcp_settings = Settings(lifespan=server_lifespan)
    mcp = FastMCP(**mcp_settings.model_dump())
    for tool in agent.tools.values():
        mcp._tool_manager._tools[tool.get_name()] = make_tool(tool)

    return mcp


def start_mcp_server() -> None:
    create_mcp_server().run()

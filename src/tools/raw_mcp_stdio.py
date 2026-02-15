"""Custom MCP stdio tool that avoids AsyncExitStack deadlock.

MCPStdioTool uses AsyncExitStack.enter_async_context() to manage the
stdio_client transport and ClientSession. For the Go-based GitHub MCP
server, this breaks the anyio task group hierarchy and causes call_tool()
to hang indefinitely.

This wrapper runs the transport + session in a background task with
proper `async with` nesting, then bridges tool calls via asyncio queues.
It exposes the same interface the Agent expects: `.functions` list of
FunctionTool, async context manager protocol, and `.is_connected`.
"""

import asyncio
import logging
from functools import partial
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from agent_framework import FunctionTool
from agent_framework._mcp import _get_input_model_from_mcp_tool, _normalize_mcp_name

logger = logging.getLogger(__name__)


class RawMCPStdioTool:
    """MCP stdio tool using raw ClientSession with proper async-with nesting.

    Compatible with agent_framework.Agent — exposes `.functions` and
    acts as an async context manager.
    """

    def __init__(
        self,
        name: str,
        command: str,
        *,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env
        self.allowed_tools = allowed_tools

        self.is_connected = False
        self._functions: list[FunctionTool] = []

        # Internals for the background task bridge
        self._bg_task: asyncio.Task | None = None
        self._ready_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._session: ClientSession | None = None

        # Queue for tool call requests/responses
        self._call_queue: asyncio.Queue[tuple[str, dict, asyncio.Future]] = asyncio.Queue()

    @property
    def functions(self) -> list[FunctionTool]:
        if not self.allowed_tools:
            return self._functions
        return [f for f in self._functions if f.name in self.allowed_tools]

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def connect(self) -> None:
        """Start background task that manages the MCP session."""
        if self.is_connected:
            return

        self._ready_event.clear()
        self._stop_event.clear()
        self._bg_task = asyncio.create_task(self._run_session())

        # Wait for session to be ready (tools loaded)
        await self._ready_event.wait()

        if not self.is_connected:
            # Background task failed during init
            if self._bg_task.done():
                self._bg_task.result()  # re-raise exception
            raise RuntimeError("Failed to connect to MCP server")

    async def close(self) -> None:
        """Signal the background task to stop and wait for cleanup."""
        if self._bg_task and not self._bg_task.done():
            self._stop_event.set()
            try:
                await asyncio.wait_for(self._bg_task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._bg_task.cancel()
                try:
                    await self._bg_task
                except asyncio.CancelledError:
                    pass
        self.is_connected = False
        self._session = None
        self._functions.clear()

    async def _run_session(self) -> None:
        """Background task: open transport + session with proper nesting."""
        server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env,
        )

        try:
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session

                    # Load tools
                    await self._load_tools(session)

                    self.is_connected = True
                    self._ready_event.set()

                    # Process tool calls until stopped
                    await self._process_calls(session)
        except Exception:
            logger.exception("MCP session failed for %s", self.name)
            self._ready_event.set()  # unblock connect() even on failure
            raise

    async def _load_tools(self, session: ClientSession) -> None:
        """Load tools from the MCP server and create FunctionTool instances."""
        cursor = None
        while True:
            params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
            tool_list = await session.list_tools(params=params)

            for tool in tool_list.tools:
                local_name = _normalize_mcp_name(tool.name)
                input_model = _get_input_model_from_mcp_tool(tool)

                func_tool: FunctionTool = FunctionTool(
                    func=partial(self._call_tool_bridged, tool.name),
                    name=local_name,
                    description=tool.description or "",
                    input_model=input_model,
                )
                self._functions.append(func_tool)

            if not tool_list.nextCursor:
                break
            cursor = tool_list.nextCursor

        logger.info("Loaded %d tools from %s", len(self._functions), self.name)

    async def _process_calls(self, session: ClientSession) -> None:
        """Process queued tool calls until stop is signaled."""
        while not self._stop_event.is_set():
            try:
                # Check for call requests with a short timeout so we can
                # also check the stop event periodically
                tool_name, kwargs, future = await asyncio.wait_for(self._call_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            try:
                result = await session.call_tool(tool_name, arguments=kwargs)
                text = _parse_tool_result(result)
                future.set_result(text)
            except Exception as exc:
                future.set_exception(exc)

    async def _call_tool_bridged(self, tool_name: str, **kwargs: Any) -> str:
        """Bridge a tool call from the Agent to the background session task.

        This is called by FunctionTool.invoke() in the Agent's task context.
        We send the request to the background task (which owns the session)
        and await the result.
        """
        # Filter framework kwargs that shouldn't be sent to MCP
        filtered = {
            k: v
            for k, v in kwargs.items()
            if k
            not in {
                "chat_options",
                "tools",
                "tool_choice",
                "session",
                "thread",
                "conversation_id",
                "options",
                "response_format",
            }
        }

        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        await self._call_queue.put((tool_name, filtered, future))
        return await future


def _parse_tool_result(result: types.CallToolResult) -> str:
    """Convert MCP CallToolResult to a string."""
    parts = []
    for content in result.content:
        if isinstance(content, types.TextContent):
            parts.append(content.text)
        elif isinstance(content, types.ImageContent):
            parts.append(f"[image: {content.mimeType}]")
        elif isinstance(content, types.EmbeddedResource):
            parts.append(str(content))
        else:
            parts.append(str(content))
    return "\n".join(parts)

"""
Clawdbot Gateway WebSocket Client.

Connects to Clawdbot's gateway to run agent tasks with human-in-loop support.
Falls back gracefully if Clawdbot is not running.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, AsyncIterator

import structlog

if TYPE_CHECKING:
    from websockets.client import WebSocketClientProtocol

logger = structlog.get_logger()


class AgentEventType(str, Enum):
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    TEXT = "text"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class AgentEvent:
    """Event from streaming agent execution."""

    event_type: AgentEventType
    content: str | None = None
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_result: str | None = None


@dataclass
class AgentResult:
    """Final result from agent execution."""

    success: bool
    response: str
    events: list[AgentEvent] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0


def clawdbot_available() -> bool:
    """Quick check if Clawdbot gateway is reachable."""
    import socket

    from dealintel.config import settings

    if not settings.clawdbot_enabled:
        return False

    try:
        # Parse host/port from WebSocket URL
        url = settings.clawdbot_gateway_url
        # ws://127.0.0.1:18789 -> 127.0.0.1, 18789
        if url.startswith("ws://"):
            url = url[5:]
        elif url.startswith("wss://"):
            url = url[6:]
        host, port = url.split(":")
        port = int(port.split("/")[0])

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


class ClawdbotClient:
    """
    WebSocket client for Clawdbot Gateway.

    Usage:
        async with ClawdbotClient() as client:
            result = await client.run_agent("Navigate to example.com")
            print(result.response)
    """

    def __init__(
        self,
        gateway_url: str | None = None,
        token: str | None = None,
        connect_timeout: float = 10.0,
    ):
        from dealintel.config import settings

        self.gateway_url = gateway_url or settings.clawdbot_gateway_url
        self.token = token or settings.clawdbot_token
        self.connect_timeout = connect_timeout

        self._ws: WebSocketClientProtocol | None = None
        self._connected = False
        self._request_id = 0

    async def __aenter__(self) -> ClawdbotClient:
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()

    def _next_request_id(self) -> str:
        self._request_id += 1
        return f"deals-bot-{self._request_id}"

    async def connect(self) -> None:
        """Establish connection and complete handshake."""
        if self._connected:
            return

        import websockets

        logger.info("clawdbot.connecting", url=self.gateway_url)

        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    self.gateway_url,
                    ping_interval=30,
                    ping_timeout=10,
                ),
                timeout=self.connect_timeout,
            )
        except asyncio.TimeoutError:
            raise ConnectionError(f"Timeout connecting to {self.gateway_url}")
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Clawdbot: {e}")

        # Send handshake
        req_id = self._next_request_id()
        handshake = {
            "type": "req",
            "id": req_id,
            "method": "connect",
            "params": {
                "minProtocol": 1,
                "maxProtocol": 1,
                "client": {
                    "name": "deals-bot",
                    "version": "1.0.0",
                    "platform": "python",
                    "mode": "automation",
                },
            },
        }

        if self.token:
            handshake["params"]["auth"] = {"token": self.token}

        await self._ws.send(json.dumps(handshake))

        # Wait for hello-ok
        raw = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
        response = json.loads(raw)

        if response.get("type") != "res" or not response.get("ok"):
            error = response.get("error", {}).get("message", "Unknown error")
            raise ConnectionError(f"Clawdbot handshake failed: {error}")

        self._connected = True
        logger.info("clawdbot.connected")

    async def disconnect(self) -> None:
        """Close the connection."""
        self._connected = False

        if self._ws:
            await self._ws.close()
            self._ws = None

        logger.debug("clawdbot.disconnected")

    async def run_agent(
        self,
        message: str,
        timeout_seconds: float | None = None,
    ) -> AgentResult:
        """
        Run an agent task and wait for completion.

        Args:
            message: The task/prompt for the agent
            timeout_seconds: Max time to wait (default from settings)

        Returns:
            AgentResult with success status and response
        """
        from dealintel.config import settings

        if not self._connected:
            await self.connect()

        assert self._ws is not None

        import time

        start_time = time.time()
        timeout = timeout_seconds or settings.clawdbot_timeout_seconds

        req_id = self._next_request_id()
        idem_key = str(uuid.uuid4())

        events: list[AgentEvent] = []

        # Send agent request
        request = {
            "type": "req",
            "id": req_id,
            "method": "agent",
            "params": {
                "message": message,
                "idempotencyKey": idem_key,
            },
        }

        logger.info("clawdbot.agent.start", message=message[:100])
        await self._ws.send(json.dumps(request))

        try:
            # Collect events until we get final response
            async with asyncio.timeout(timeout):
                while True:
                    raw = await self._ws.recv()
                    msg = json.loads(raw)

                    if msg.get("type") == "event" and msg.get("event") == "agent":
                        payload = msg.get("payload", {})
                        event_type_str = payload.get("type", "text")
                        try:
                            event_type = AgentEventType(event_type_str)
                        except ValueError:
                            event_type = AgentEventType.TEXT

                        event = AgentEvent(
                            event_type=event_type,
                            content=payload.get("content"),
                            tool_name=payload.get("toolName"),
                            tool_input=payload.get("toolInput"),
                            tool_result=payload.get("toolResult"),
                        )
                        events.append(event)

                    elif msg.get("type") == "res" and msg.get("id") == req_id:
                        duration = time.time() - start_time

                        if msg.get("ok"):
                            payload = msg.get("payload", {})
                            logger.info(
                                "clawdbot.agent.complete",
                                duration=round(duration, 1),
                                events=len(events),
                            )
                            return AgentResult(
                                success=True,
                                response=payload.get("summary", ""),
                                events=events,
                                duration_seconds=duration,
                            )
                        else:
                            error = msg.get("error", {}).get("message", "Unknown")
                            logger.error("clawdbot.agent.failed", error=error)
                            return AgentResult(
                                success=False,
                                response="",
                                events=events,
                                error=error,
                                duration_seconds=duration,
                            )

        except asyncio.TimeoutError:
            duration = time.time() - start_time
            logger.error("clawdbot.agent.timeout", timeout=timeout)
            return AgentResult(
                success=False,
                response="",
                events=events,
                error=f"Timeout after {timeout}s",
                duration_seconds=duration,
            )


@asynccontextmanager
async def clawdbot_client() -> AsyncIterator[ClawdbotClient]:
    """Context manager for Clawdbot client."""
    client = ClawdbotClient()
    try:
        await client.connect()
        yield client
    finally:
        await client.disconnect()

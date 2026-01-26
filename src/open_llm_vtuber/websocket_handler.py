from typing import Dict, List, Optional, Callable, TypedDict
from fastapi import WebSocket, WebSocketDisconnect, HTTPException
import asyncio
import json
import time
from enum import Enum
import numpy as np
from loguru import logger

from .service_context import ServiceContext
from .security.input_validation import validate_websocket_message
from .chat_group import (
    ChatGroupManager,
    handle_group_operation,
    handle_client_disconnect,
    broadcast_to_group,
)
from .message_handler import message_handler
from .utils.stream_audio import prepare_audio_payload
from .chat_history_manager import (
    create_new_history,
    get_history,
    delete_history,
    get_history_list,
)
from .config_manager.utils import scan_config_alts_directory, scan_bg_directory
from .conversations.conversation_handler import (
    handle_conversation_trigger,
    handle_group_interrupt,
    handle_individual_interrupt,
)


class MessageType(Enum):
    """Enum for WebSocket message types"""

    GROUP = ["add-client-to-group", "remove-client-from-group"]
    HISTORY = [
        "fetch-history-list",
        "fetch-and-set-history",
        "create-new-history",
        "delete-history",
    ]
    CONVERSATION = ["mic-audio-end", "text-input", "ai-speak-signal"]
    CONFIG = ["fetch-configs", "switch-config"]
    CONTROL = ["interrupt-signal", "audio-play-start"]
    DATA = ["mic-audio-data"]


class WSMessage(TypedDict, total=False):
    """Type definition for WebSocket messages"""

    type: str
    action: Optional[str]
    text: Optional[str]
    audio: Optional[List[float]]
    images: Optional[List[str]]
    history_uid: Optional[str]
    file: Optional[str]
    display_text: Optional[dict]


class WebSocketHandler:
    """Handles WebSocket connections and message routing"""

    def __init__(self, default_context_cache: ServiceContext):
        """Initialize the WebSocket handler with default context"""
        self.client_connections: Dict[str, WebSocket] = {}
        self.client_contexts: Dict[str, ServiceContext] = {}
        self.chat_group_manager = ChatGroupManager()
        self.current_conversation_tasks: Dict[str, Optional[asyncio.Task]] = {}
        self.default_context_cache = default_context_cache
        self.received_data_buffers: Dict[str, np.ndarray] = {}

        # Message handlers mapping
        self._message_handlers = self._init_message_handlers()

    def _init_message_handlers(self) -> Dict[str, Callable]:
        """Initialize message type to handler mapping"""
        return {
            "add-client-to-group": self._handle_group_operation,
            "remove-client-from-group": self._handle_group_operation,
            "request-group-info": self._handle_group_info,
            "fetch-history-list": self._handle_history_list_request,
            "fetch-and-set-history": self._handle_fetch_history,
            "create-new-history": self._handle_create_history,
            "delete-history": self._handle_delete_history,
            "interrupt-signal": self._handle_interrupt,
            "mic-audio-data": self._handle_audio_data,
            "mic-audio-end": self._handle_conversation_trigger,
            "raw-audio-data": self._handle_raw_audio_data,
            "text-input": self._handle_conversation_trigger,
            "ai-speak-signal": self._handle_conversation_trigger,
            "fetch-configs": self._handle_fetch_configs,
            "switch-config": self._handle_config_switch,
            "fetch-backgrounds": self._handle_fetch_backgrounds,
            "audio-play-start": self._handle_audio_play_start,
            "request-init-config": self._handle_init_config_request,
            "heartbeat": self._handle_heartbeat,
        }

    async def handle_new_connection(
        self, websocket: WebSocket, client_uid: str
    ) -> None:
        """
        Handle new WebSocket connection setup

        Args:
            websocket: The WebSocket connection
            client_uid: Unique identifier for the client

        Raises:
            Exception: If initialization fails
        """
        logger.info(f"[WS DEBUG] Client {client_uid} - Starting connection initialization")
        logger.info(f"[WS DEBUG] Client {client_uid} - Connection state: {websocket.client_state.name}")
        
        try:
            logger.debug(f"[WS DEBUG] Client {client_uid} - Initializing service context...")
            session_service_context = await self._init_service_context(
                websocket.send_text, client_uid
            )
            logger.debug(f"[WS DEBUG] Client {client_uid} - Service context initialized")

            logger.debug(f"[WS DEBUG] Client {client_uid} - Storing client data...")
            await self._store_client_data(
                websocket, client_uid, session_service_context
            )
            logger.debug(f"[WS DEBUG] Client {client_uid} - Client data stored")

            logger.debug(f"[WS DEBUG] Client {client_uid} - Sending initial messages...")
            await self._send_initial_messages(
                websocket, client_uid, session_service_context
            )
            logger.debug(f"[WS DEBUG] Client {client_uid} - Initial messages sent")

            logger.info(f"[WS DEBUG] Client {client_uid} - Connection established successfully")
            logger.info(f"[WS DEBUG] Client {client_uid} - Connection state after init: {websocket.client_state.name}")

        except Exception as e:
            logger.error(
                f"[WS DEBUG] Client {client_uid} - Failed to initialize connection: {e}", exc_info=True
            )
            logger.error(f"[WS DEBUG] Client {client_uid} - Connection state at init error: {websocket.client_state.name}")
            await self._cleanup_failed_connection(client_uid)
            raise

    async def _store_client_data(
        self,
        websocket: WebSocket,
        client_uid: str,
        session_service_context: ServiceContext,
    ):
        """Store client data and initialize group status"""
        self.client_connections[client_uid] = websocket
        self.client_contexts[client_uid] = session_service_context
        self.received_data_buffers[client_uid] = np.array([])

        self.chat_group_manager.client_group_map[client_uid] = ""
        await self.send_group_update(websocket, client_uid)

    async def _send_initial_messages(
        self,
        websocket: WebSocket,
        client_uid: str,
        session_service_context: ServiceContext,
    ):
        """Send initial connection messages to the client"""
        logger.info(f"[WS DEBUG] Client {client_uid} - Preparing to send initial messages")
        logger.info(f"[WS DEBUG] Client {client_uid} - Connection state before sending: {websocket.client_state.name}")
        
        try:
            logger.debug(f"[WS DEBUG] Client {client_uid} - Sending connection established message...")
            await websocket.send_text(
                json.dumps({"type": "full-text", "text": "Connection established"})
            )
            logger.debug(f"[WS DEBUG] Client {client_uid} - Connection established message sent")
        except Exception as send_err:
            logger.error(f"[WS DEBUG] Client {client_uid} - Failed to send connection message: {send_err}", exc_info=True)
            raise

        # Debug: Log what we're sending
        model_info_to_send = session_service_context.live2d_model.model_info
        logger.info(f"[DEBUG] Sending model_info to client:")
        logger.info(f"[DEBUG] model_info type: {type(model_info_to_send)}")
        logger.info(f"[DEBUG] model_info content: {json.dumps(model_info_to_send, indent=2)}")
        if model_info_to_send:
            logger.info(f"[DEBUG] model_info.url: {model_info_to_send.get('url', 'MISSING')}")
            logger.info(f"[DEBUG] model_info.name: {model_info_to_send.get('name', 'MISSING')}")
        else:
            logger.error("[DEBUG] model_info is None or empty!")
        
        message_to_send = {
            "type": "set-model-and-conf",
            "model_info": model_info_to_send,
            "conf_name": session_service_context.character_config.conf_name,
            "conf_uid": session_service_context.character_config.conf_uid,
            "client_uid": client_uid,
        }
        logger.info(f"[DEBUG] Full WebSocket message: {json.dumps(message_to_send, indent=2)}")
        
        try:
            logger.debug(f"[WS DEBUG] Client {client_uid} - Sending set-model-and-conf message...")
            await websocket.send_text(json.dumps(message_to_send))
            logger.debug(f"[WS DEBUG] Client {client_uid} - set-model-and-conf message sent")
        except Exception as send_err:
            logger.error(f"[WS DEBUG] Client {client_uid} - Failed to send model config: {send_err}", exc_info=True)
            raise

        # Send initial group status
        try:
            logger.debug(f"[WS DEBUG] Client {client_uid} - Sending group update...")
            await self.send_group_update(websocket, client_uid)
            logger.debug(f"[WS DEBUG] Client {client_uid} - Group update sent")
        except Exception as send_err:
            logger.error(f"[WS DEBUG] Client {client_uid} - Failed to send group update: {send_err}", exc_info=True)
            raise

        # Start microphone
        try:
            logger.debug(f"[WS DEBUG] Client {client_uid} - Sending start-mic control...")
            await websocket.send_text(json.dumps({"type": "control", "text": "start-mic"}))
            logger.debug(f"[WS DEBUG] Client {client_uid} - start-mic control sent")
        except Exception as send_err:
            logger.error(f"[WS DEBUG] Client {client_uid} - Failed to send start-mic: {send_err}", exc_info=True)
            raise
        
        logger.info(f"[WS DEBUG] Client {client_uid} - All initial messages sent successfully")

    async def _init_service_context(
        self, send_text: Callable, client_uid: str
    ) -> ServiceContext:
        """Initialize service context for a new session by cloning the default context"""
        session_service_context = ServiceContext()
        await session_service_context.load_cache(
            config=self.default_context_cache.config.model_copy(deep=True),
            system_config=self.default_context_cache.system_config.model_copy(
                deep=True
            ),
            character_config=self.default_context_cache.character_config.model_copy(
                deep=True
            ),
            live2d_model=self.default_context_cache.live2d_model,
            asr_engine=self.default_context_cache.asr_engine,
            tts_engine=self.default_context_cache.tts_engine,
            vad_engine=self.default_context_cache.vad_engine,
            agent_engine=self.default_context_cache.agent_engine,
            translate_engine=self.default_context_cache.translate_engine,
            mcp_server_registery=self.default_context_cache.mcp_server_registery,
            tool_adapter=self.default_context_cache.tool_adapter,
            send_text=send_text,
            client_uid=client_uid,
        )
        return session_service_context

    async def handle_websocket_communication(
        self, websocket: WebSocket, client_uid: str
    ) -> None:
        """
        Handle ongoing WebSocket communication

        Args:
            websocket: The WebSocket connection
            client_uid: Unique identifier for the client
        """
        logger.info(f"[WS DEBUG] Starting communication loop for client {client_uid}")
        message_count = 0
        
        try:
            loop_start_time = time.time()
            last_activity_time = time.time()
            # #region agent log
            with open("debug.log", "a") as logf:
                logf.write(json.dumps({"id": f"log_{int(time.time() * 1000)}", "timestamp": int(time.time() * 1000), "location": "websocket_handler.py:269", "message": "Communication loop started", "data": {"client_uid": client_uid, "start_time": loop_start_time}, "sessionId": "debug-session", "runId": "run1", "hypothesisId": "H3"}) + "\n")
            # #endregion
            while True:
                # Check if connection is still open before receiving
                # Note: client_state can be CONNECTED, DISCONNECTED, or CONNECTING
                try:
                    connection_state = websocket.client_state.name
                    current_time = time.time()
                    elapsed_time = current_time - loop_start_time
                    # #region agent log
                    with open("debug.log", "a") as logf:
                        log_entry = {
                            "id": f"log_{int(time.time() * 1000)}",
                            "timestamp": int(time.time() * 1000),
                            "location": "websocket_handler.py:273",
                            "message": "Connection state check",
                            "data": {
                                "client_uid": client_uid,
                                "connection_state": connection_state,
                                "message_count": message_count,
                                "elapsed_time": elapsed_time,
                                "idle_time": current_time - last_activity_time
                            },
                            "sessionId": "debug-session",
                            "runId": "run1",
                            "hypothesisId": "H1"
                        }
                        logf.write(json.dumps(log_entry) + "\n")
                    # #endregion
                    logger.debug(f"[WS DEBUG] Client {client_uid} - Connection state: {connection_state}, Message count: {message_count}, Elapsed: {elapsed_time:.1f}s")
                except Exception as state_err:
                    logger.warning(f"[WS DEBUG] Client {client_uid} - Could not check connection state: {state_err}")
                    # Continue anyway - let receive_json() handle the disconnect
                
                # Don't break on state check - let receive_json() raise WebSocketDisconnect if closed
                    
                try:
                    logger.debug(f"[WS DEBUG] Client {client_uid} - Waiting for message...")
                    receive_start_time = time.time()
                    idle_time = receive_start_time - last_activity_time
                    # #region agent log
                    with open("debug.log", "a") as logf:
                        logf.write(json.dumps({"id": f"log_{int(time.time() * 1000)}", "timestamp": int(time.time() * 1000), "location": "websocket_handler.py:283", "message": "Before receive_json", "data": {"client_uid": client_uid, "idle_time": idle_time, "connection_state": websocket.client_state.name if hasattr(websocket, "client_state") else "unknown"}, "sessionId": "debug-session", "runId": "run1", "hypothesisId": "H3"}) + "\n")
                    # #endregion
                    try:
                        raw_data = await websocket.receive_json()
                        receive_duration = time.time() - receive_start_time
                        last_activity_time = time.time()
                        # #region agent log
                        with open("debug.log", "a") as logf:
                            logf.write(json.dumps({"id": f"log_{int(time.time() * 1000)}", "timestamp": int(time.time() * 1000), "location": "websocket_handler.py:284", "message": "After receive_json success", "data": {"client_uid": client_uid, "receive_duration": receive_duration, "message_type": raw_data.get("type") if isinstance(raw_data, dict) else "unknown"}, "sessionId": "debug-session", "runId": "run1", "hypothesisId": "H3"}) + "\n")
                        # #endregion
                    except WebSocketDisconnect as ws_err:
                        disconnect_time = time.time()
                        total_connection_time = disconnect_time - loop_start_time
                        # #region agent log
                        with open("debug.log", "a") as logf:
                            logf.write(json.dumps({"id": f"log_{int(time.time() * 1000)}", "timestamp": int(time.time() * 1000), "location": "websocket_handler.py:283", "message": "WebSocketDisconnect in receive_json", "data": {"client_uid": client_uid, "total_connection_time": total_connection_time, "idle_time": disconnect_time - last_activity_time, "error": str(ws_err), "code": ws_err.code if hasattr(ws_err, "code") else None}, "sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1"}) + "\n")
                        # #endregion
                        raise
                    message_count += 1
                    logger.info(f"[WS DEBUG] Client {client_uid} - Received message #{message_count}: type={raw_data.get('type')}, keys={list(raw_data.keys())}")
                    
                    # Validate and sanitize input before processing
                    try:
                        logger.debug(f"[WS DEBUG] Client {client_uid} - Validating message...")
                        data = validate_websocket_message(raw_data)
                        logger.info(f"[WS DEBUG] Client {client_uid} - Message validated successfully: type={data.get('type')}")
                    except HTTPException as e:
                        logger.warning(f"[WS DEBUG] Client {client_uid} - Invalid message: {e.detail}")
                        logger.debug(f"[WS DEBUG] Client {client_uid} - Raw message that failed validation: {raw_data}")
                        try:
                            logger.debug(f"[WS DEBUG] Client {client_uid} - Sending validation error response...")
                            await websocket.send_text(
                                json.dumps({
                                    "type": "error",
                                    "message": "Invalid message format",
                                    "details": e.detail if isinstance(e.detail, dict) else {"error": str(e.detail)}
                                })
                            )
                            logger.debug(f"[WS DEBUG] Client {client_uid} - Validation error response sent")
                        except Exception as send_err:
                            logger.error(f"[WS DEBUG] Client {client_uid} - Failed to send error message: {send_err}", exc_info=True)
                            logger.error(f"[WS DEBUG] Client {client_uid} - Connection state when send failed: {websocket.client_state.name}")
                        continue  # Don't close connection, just skip this message
                    except Exception as validation_err:
                        logger.error(f"Validation error (not HTTPException) for client {client_uid}: {validation_err}")
                        logger.debug(f"Raw message: {raw_data}")
                        try:
                            await websocket.send_text(
                                json.dumps({
                                    "type": "error",
                                    "message": "Invalid message format",
                                    "details": {"error": str(validation_err)}
                                })
                            )
                        except Exception as send_err:
                            logger.error(f"Failed to send error message to client {client_uid}: {send_err}")
                        continue  # Don't close connection, just skip this message
                    
                    logger.debug(f"[WS DEBUG] Client {client_uid} - Processing message type: {data.get('type')}")
                    message_handler.handle_message(client_uid, data)
                    logger.debug(f"[WS DEBUG] Client {client_uid} - Routing message...")
                    await self._route_message(websocket, client_uid, data)
                    logger.debug(f"[WS DEBUG] Client {client_uid} - Message processed successfully")
                except WebSocketDisconnect as ws_disconnect:
                    logger.info(f"[WS DEBUG] Client {client_uid} - WebSocketDisconnect exception: {ws_disconnect}")
                    raise
                except json.JSONDecodeError as json_err:
                    logger.error(f"[WS DEBUG] Client {client_uid} - Invalid JSON received: {json_err}")
                    try:
                        await websocket.send_text(
                            json.dumps({"type": "error", "message": "Invalid JSON format"})
                        )
                    except Exception as send_err:
                        logger.error(f"[WS DEBUG] Client {client_uid} - Failed to send JSON error: {send_err}")
                    continue
                except HTTPException as http_err:
                    # HTTPException from validation - already handled above, but if it gets here, log and continue
                    logger.warning(f"HTTPException in message processing for client {client_uid}: {http_err}")
                    try:
                        await websocket.send_text(
                            json.dumps({"type": "error", "message": "Invalid request format"})
                        )
                    except Exception:
                        pass  # Ignore if we can't send
                    continue  # Don't close connection
                except Exception as e:
                    logger.error(f"Error processing message for client {client_uid}: {e}", exc_info=True)
                    try:
                        await websocket.send_text(
                            json.dumps({"type": "error", "message": f"Error processing message: {str(e)}"})
                        )
                    except Exception as send_err:
                        logger.error(f"Failed to send error message: {send_err}")
                    continue  # Don't close connection on message processing errors

        except WebSocketDisconnect as ws_disconnect:
            disconnect_time = time.time()
            total_connection_time = disconnect_time - loop_start_time if 'loop_start_time' in locals() else 0
            # #region agent log
            with open("debug.log", "a") as logf:
                logf.write(json.dumps({"id": f"log_{int(time.time() * 1000)}", "timestamp": int(time.time() * 1000), "location": "websocket_handler.py:361", "message": "WebSocketDisconnect caught", "data": {"client_uid": client_uid, "total_connection_time": total_connection_time, "messages_processed": message_count, "error": str(ws_disconnect), "code": ws_disconnect.code if hasattr(ws_disconnect, "code") else None, "reason": ws_disconnect.reason if hasattr(ws_disconnect, "reason") else None}, "sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1"}) + "\n")
            # #endregion
            logger.info(f"[WS DEBUG] Client {client_uid} - WebSocket disconnected normally")
            logger.info(f"[WS DEBUG] Client {client_uid} - Total messages processed: {message_count}")
            raise
        except Exception as e:
            logger.error(f"[WS DEBUG] Client {client_uid} - Fatal error in WebSocket communication: {e}", exc_info=True)
            logger.error(f"[WS DEBUG] Client {client_uid} - Connection state at fatal error: {websocket.client_state.name}")
            logger.error(f"[WS DEBUG] Client {client_uid} - Total messages processed before error: {message_count}")
            raise

    async def _route_message(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """
        Route incoming message to appropriate handler

        Args:
            websocket: The WebSocket connection
            client_uid: Client identifier
            data: Message data
        """
        msg_type = data.get("type")
        if not msg_type:
            logger.warning("Message received without type")
            return
        
        logger.debug(f"Routing message type '{msg_type}' for client {client_uid}")

        handler = self._message_handlers.get(msg_type)
        if handler:
            await handler(websocket, client_uid, data)
        else:
            if msg_type != "frontend-playback-complete":
                logger.warning(f"Unknown message type: {msg_type}")

    async def _handle_group_operation(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        """Handle group-related operations"""
        operation = data.get("type")
        target_uid = data.get(
            "invitee_uid" if operation == "add-client-to-group" else "target_uid"
        )

        await handle_group_operation(
            operation=operation,
            client_uid=client_uid,
            target_uid=target_uid,
            chat_group_manager=self.chat_group_manager,
            client_connections=self.client_connections,
            send_group_update=self.send_group_update,
        )

    async def handle_disconnect(self, client_uid: str) -> None:
        """Handle client disconnection"""
        group = self.chat_group_manager.get_client_group(client_uid)
        if group:
            await handle_group_interrupt(
                group_id=group.group_id,
                heard_response="",
                current_conversation_tasks=self.current_conversation_tasks,
                chat_group_manager=self.chat_group_manager,
                client_contexts=self.client_contexts,
                broadcast_to_group=self.broadcast_to_group,
            )

        await handle_client_disconnect(
            client_uid=client_uid,
            chat_group_manager=self.chat_group_manager,
            client_connections=self.client_connections,
            send_group_update=self.send_group_update,
        )

        # Clean up other client data
        self.client_connections.pop(client_uid, None)
        self.client_contexts.pop(client_uid, None)
        self.received_data_buffers.pop(client_uid, None)
        if client_uid in self.current_conversation_tasks:
            task = self.current_conversation_tasks[client_uid]
            if task and not task.done():
                task.cancel()
            self.current_conversation_tasks.pop(client_uid, None)

        # Call context close to clean up resources (e.g., MCPClient)
        context = self.client_contexts.get(client_uid)
        if context:
            await context.close()

        logger.info(f"Client {client_uid} disconnected")
        message_handler.cleanup_client(client_uid)

    async def _cleanup_failed_connection(self, client_uid: str) -> None:
        """Clean up failed connection data"""
        self.client_connections.pop(client_uid, None)
        self.client_contexts.pop(client_uid, None)
        self.received_data_buffers.pop(client_uid, None)
        self.chat_group_manager.client_group_map.pop(client_uid, None)

        if client_uid in self.current_conversation_tasks:
            task = self.current_conversation_tasks[client_uid]
            if task and not task.done():
                task.cancel()
            self.current_conversation_tasks.pop(client_uid, None)

        message_handler.cleanup_client(client_uid)

    async def broadcast_to_group(
        self, group_members: list[str], message: dict, exclude_uid: str = None
    ) -> None:
        """Broadcasts a message to group members"""
        await broadcast_to_group(
            group_members=group_members,
            message=message,
            client_connections=self.client_connections,
            exclude_uid=exclude_uid,
        )

    async def send_group_update(self, websocket: WebSocket, client_uid: str):
        """Sends group information to a client"""
        logger.debug(f"[WS DEBUG] Client {client_uid} - Sending group update, connection state: {websocket.client_state.name}")
        group = self.chat_group_manager.get_client_group(client_uid)
        if group:
            current_members = self.chat_group_manager.get_group_members(client_uid)
            try:
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "group-update",
                            "members": current_members,
                            "is_owner": group.owner_uid == client_uid,
                        }
                    )
                )
                logger.debug(f"[WS DEBUG] Client {client_uid} - Group update sent (group exists)")
            except Exception as send_err:
                logger.error(f"[WS DEBUG] Client {client_uid} - Failed to send group update: {send_err}", exc_info=True)
                raise
        else:
            try:
                logger.debug(f"[WS DEBUG] Client {client_uid} - Sending empty group update (no group)")
                await websocket.send_text(
                    json.dumps(
                        {
                        "type": "group-update",
                        "members": [],
                        "is_owner": False,
                    }
                )
            )
                logger.debug(f"[WS DEBUG] Client {client_uid} - Empty group update sent")
            except Exception as send_err:
                logger.error(f"[WS DEBUG] Client {client_uid} - Failed to send empty group update: {send_err}", exc_info=True)
                raise

    async def _handle_interrupt(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle conversation interruption"""
        heard_response = data.get("text", "")
        context = self.client_contexts[client_uid]
        group = self.chat_group_manager.get_client_group(client_uid)

        if group and len(group.members) > 1:
            await handle_group_interrupt(
                group_id=group.group_id,
                heard_response=heard_response,
                current_conversation_tasks=self.current_conversation_tasks,
                chat_group_manager=self.chat_group_manager,
                client_contexts=self.client_contexts,
                broadcast_to_group=self.broadcast_to_group,
            )
        else:
            await handle_individual_interrupt(
                client_uid=client_uid,
                current_conversation_tasks=self.current_conversation_tasks,
                context=context,
                heard_response=heard_response,
            )

    async def _handle_history_list_request(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle request for chat history list"""
        context = self.client_contexts[client_uid]
        histories = get_history_list(context.character_config.conf_uid)
        await websocket.send_text(
            json.dumps({"type": "history-list", "histories": histories})
        )

    async def _handle_fetch_history(
        self, websocket: WebSocket, client_uid: str, data: dict
    ):
        """Handle fetching and setting specific chat history"""
        history_uid = data.get("history_uid")
        if not history_uid:
            return

        context = self.client_contexts[client_uid]
        # Update history_uid in service context
        context.history_uid = history_uid
        context.agent_engine.set_memory_from_history(
            conf_uid=context.character_config.conf_uid,
            history_uid=history_uid,
        )

        messages = [
            msg
            for msg in get_history(
                context.character_config.conf_uid,
                history_uid,
            )
            if msg["role"] != "system"
        ]
        await websocket.send_text(
            json.dumps({"type": "history-data", "messages": messages})
        )

    async def _handle_create_history(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle creation of new chat history"""
        context = self.client_contexts[client_uid]
        history_uid = create_new_history(context.character_config.conf_uid)
        if history_uid:
            context.history_uid = history_uid
            context.agent_engine.set_memory_from_history(
                conf_uid=context.character_config.conf_uid,
                history_uid=history_uid,
            )
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "new-history-created",
                        "history_uid": history_uid,
                    }
                )
            )

    async def _handle_delete_history(
        self, websocket: WebSocket, client_uid: str, data: dict
    ):
        """Handle deletion of chat history"""
        history_uid = data.get("history_uid")
        if not history_uid:
            return

        context = self.client_contexts[client_uid]
        success = delete_history(
            context.character_config.conf_uid,
            history_uid,
        )
        await websocket.send_text(
            json.dumps(
                {
                    "type": "history-deleted",
                    "success": success,
                    "history_uid": history_uid,
                }
            )
        )
        if history_uid == context.history_uid:
            context.history_uid = None

    async def _handle_audio_data(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle incoming audio data"""
        audio_data = data.get("audio", [])
        if audio_data:
            self.received_data_buffers[client_uid] = np.append(
                self.received_data_buffers[client_uid],
                np.array(audio_data, dtype=np.float32),
            )

    async def _handle_raw_audio_data(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle incoming raw audio data for VAD processing"""
        context = self.client_contexts[client_uid]
        chunk = data.get("audio", [])
        if chunk:
            for audio_bytes in context.vad_engine.detect_speech(chunk):
                if audio_bytes == b"<|PAUSE|>":
                    await websocket.send_text(
                        json.dumps({"type": "control", "text": "interrupt"})
                    )
                elif audio_bytes == b"<|RESUME|>":
                    pass
                elif len(audio_bytes) > 1024:
                    # Detected audio activity (voice)
                    self.received_data_buffers[client_uid] = np.append(
                        self.received_data_buffers[client_uid],
                        np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32),
                    )
                    await websocket.send_text(
                        json.dumps({"type": "control", "text": "mic-audio-end"})
                    )

    async def _handle_conversation_trigger(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle triggers that start a conversation"""
        await handle_conversation_trigger(
            msg_type=data.get("type", ""),
            data=data,
            client_uid=client_uid,
            context=self.client_contexts[client_uid],
            websocket=websocket,
            client_contexts=self.client_contexts,
            client_connections=self.client_connections,
            chat_group_manager=self.chat_group_manager,
            received_data_buffers=self.received_data_buffers,
            current_conversation_tasks=self.current_conversation_tasks,
            broadcast_to_group=self.broadcast_to_group,
        )

    async def _handle_fetch_configs(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle fetching available configurations"""
        context = self.client_contexts[client_uid]
        config_files = scan_config_alts_directory(context.system_config.config_alts_dir)
        await websocket.send_text(
            json.dumps({"type": "config-files", "configs": config_files})
        )

    async def _handle_config_switch(
        self, websocket: WebSocket, client_uid: str, data: dict
    ):
        """Handle switching to a different configuration"""
        config_file_name = data.get("file")
        if config_file_name:
            context = self.client_contexts[client_uid]
            await context.handle_config_switch(websocket, config_file_name)

    async def _handle_fetch_backgrounds(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle fetching available background images"""
        bg_files = scan_bg_directory()
        await websocket.send_text(
            json.dumps({"type": "background-files", "files": bg_files})
        )

    async def _handle_audio_play_start(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """
        Handle audio playback start notification
        """
        group_members = self.chat_group_manager.get_group_members(client_uid)
        if len(group_members) > 1:
            display_text = data.get("display_text")
            if display_text:
                silent_payload = prepare_audio_payload(
                    audio_path=None,
                    display_text=display_text,
                    actions=None,
                    forwarded=True,
                )
                await self.broadcast_to_group(
                    group_members, silent_payload, exclude_uid=client_uid
                )

    async def _handle_group_info(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle group info request"""
        await self.send_group_update(websocket, client_uid)

    async def _handle_init_config_request(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle request for initialization configuration"""
        context = self.client_contexts.get(client_uid)
        if not context:
            context = self.default_context_cache

        await websocket.send_text(
            json.dumps(
                {
                    "type": "set-model-and-conf",
                    "model_info": context.live2d_model.model_info,
                    "conf_name": context.character_config.conf_name,
                    "conf_uid": context.character_config.conf_uid,
                    "client_uid": client_uid,
                }
            )
        )

    async def _handle_heartbeat(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle heartbeat messages from clients"""
        try:
            await websocket.send_json({"type": "heartbeat-ack"})
        except Exception as e:
            logger.error(f"Error sending heartbeat acknowledgment: {e}")

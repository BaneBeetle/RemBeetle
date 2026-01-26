import os
import json
import time
from uuid import uuid4
import numpy as np
from datetime import datetime
from fastapi import APIRouter, WebSocket, UploadFile, File, Response
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect
from loguru import logger
from .service_context import ServiceContext
from .websocket_handler import WebSocketHandler
from .proxy_handler import ProxyHandler
from .security.input_validation import validate_file_upload
from .security.rate_limiter import check_websocket_rate_limit


def init_client_ws_route(default_context_cache: ServiceContext) -> APIRouter:
    """
    Create and return API routes for handling the `/client-ws` WebSocket connections.

    Args:
        default_context_cache: Default service context cache for new sessions.

    Returns:
        APIRouter: Configured router with WebSocket endpoint.
    """

    router = APIRouter()
    ws_handler = WebSocketHandler(default_context_cache)

    @router.websocket("/client-ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for client connections"""
        connection_start_time = time.time()
        # #region agent log
        with open("debug.log", "a") as logf:
            logf.write(json.dumps({"id": f"log_{int(time.time() * 1000)}", "timestamp": int(time.time() * 1000), "location": "routes.py:32", "message": "WebSocket endpoint called", "data": {"client_ip": websocket.client.host if websocket.client else "unknown", "connection_start": connection_start_time}, "sessionId": "debug-session", "runId": "run1", "hypothesisId": "H4"}) + "\n")
        # #endregion
        client_ip = websocket.client.host if websocket.client else "unknown"
        logger.info(f"[WS DEBUG] WebSocket connection attempt from {client_ip} (client: {websocket.client})")
        logger.info(f"[WS DEBUG] Connection state before accept: {websocket.client_state.name}")
        
        try:
            accept_start = time.time()
            # #region agent log
            with open("debug.log", "a") as logf:
                logf.write(json.dumps({"id": f"log_{int(time.time() * 1000)}", "timestamp": int(time.time() * 1000), "location": "routes.py:39", "message": "Before websocket.accept()", "data": {"time_since_start": accept_start - connection_start_time}, "sessionId": "debug-session", "runId": "run1", "hypothesisId": "H4"}) + "\n")
            # #endregion
            await websocket.accept()
            accept_duration = time.time() - accept_start
            # #region agent log
            with open("debug.log", "a") as logf:
                logf.write(json.dumps({"id": f"log_{int(time.time() * 1000)}", "timestamp": int(time.time() * 1000), "location": "routes.py:40", "message": "After websocket.accept()", "data": {"accept_duration": accept_duration, "connection_state": websocket.client_state.name}, "sessionId": "debug-session", "runId": "run1", "hypothesisId": "H4"}) + "\n")
            # #endregion
            logger.info(f"[WS DEBUG] WebSocket connection accepted successfully")
            logger.info(f"[WS DEBUG] Connection state after accept: {websocket.client_state.name}")
            # #region agent log
            with open("debug.log", "a") as logf:
                log_entry = {
                    "id": f"log_{int(time.time() * 1000)}",
                    "timestamp": int(time.time() * 1000),
                    "location": "routes.py:42",
                    "message": "WebSocket accepted",
                    "data": {
                        "client_ip": client_ip,
                        "client_host": websocket.client.host if websocket.client else None,
                        "client_port": websocket.client.port if websocket.client else None,
                        "connection_state": websocket.client_state.name,
                        "url": str(websocket.url) if hasattr(websocket, 'url') else None,
                        "headers": dict(websocket.headers) if hasattr(websocket, 'headers') else None
                    },
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "H2"
                }
                logf.write(json.dumps(log_entry) + "\n")
            # #endregion
        except Exception as accept_err:
            logger.error(f"[WS DEBUG] Failed to accept WebSocket connection: {accept_err}", exc_info=True)
            return
        
        # Apply rate limiting after accepting connection (so we can properly close it)
        # Only rate limit if we've exceeded connection limits (not message limits)
        try:
            await check_websocket_rate_limit(websocket)
            logger.info(f"[WS DEBUG] Rate limit check passed")
        except WebSocketDisconnect:
            logger.warning("[WS DEBUG] WebSocket connection closed due to rate limit")
            return  # Connection closed due to rate limit
        except Exception as rate_limit_err:
            logger.error(f"[WS DEBUG] Error during rate limit check: {rate_limit_err}", exc_info=True)
            return
        
        client_uid = str(uuid4())
        logger.info(f"[WS DEBUG] WebSocket client {client_uid} assigned, connection state: {websocket.client_state.name}")

        try:
            logger.info(f"[WS DEBUG] Initializing new connection for client {client_uid}")
            await ws_handler.handle_new_connection(websocket, client_uid)
            logger.info(f"[WS DEBUG] Connection initialized, starting communication loop for client {client_uid}")
            await ws_handler.handle_websocket_communication(websocket, client_uid)
        except WebSocketDisconnect as ws_disconnect:
            disconnect_time = time.time()
            total_time = disconnect_time - connection_start_time
            # #region agent log
            with open("debug.log", "a") as logf:
                logf.write(json.dumps({"id": f"log_{int(time.time() * 1000)}", "timestamp": int(time.time() * 1000), "location": "routes.py:66", "message": "WebSocketDisconnect in routes", "data": {"client_uid": client_uid, "total_connection_time": total_time, "error": str(ws_disconnect), "code": ws_disconnect.code if hasattr(ws_disconnect, "code") else None}, "sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1"}) + "\n")
            # #endregion
            logger.info(f"[WS DEBUG] WebSocket disconnect for client {client_uid}: {ws_disconnect}")
            await ws_handler.handle_disconnect(client_uid)
        except Exception as e:
            logger.error(f"[WS DEBUG] Error in WebSocket connection for client {client_uid}: {e}", exc_info=True)
            logger.error(f"[WS DEBUG] Connection state at error: {websocket.client_state.name}")
            await ws_handler.handle_disconnect(client_uid)
            raise

    return router


def init_proxy_route(server_url: str) -> APIRouter:
    """
    Create and return API routes for handling proxy connections.

    Args:
        server_url: The WebSocket URL of the actual server

    Returns:
        APIRouter: Configured router with proxy WebSocket endpoint
    """
    router = APIRouter()
    proxy_handler = ProxyHandler(server_url)

    @router.websocket("/proxy-ws")
    async def proxy_endpoint(websocket: WebSocket):
        """WebSocket endpoint for proxy connections"""
        await websocket.accept()
        
        # Apply rate limiting after accepting connection
        try:
            await check_websocket_rate_limit(websocket)
        except WebSocketDisconnect:
            return  # Connection closed due to rate limit
        
        try:
            await proxy_handler.handle_client_connection(websocket)
        except Exception as e:
            logger.error(f"Error in proxy connection: {e}")
            raise

    return router


def init_webtool_routes(default_context_cache: ServiceContext) -> APIRouter:
    """
    Create and return API routes for handling web tool interactions.

    Args:
        default_context_cache: Default service context cache for new sessions.

    Returns:
        APIRouter: Configured router with WebSocket endpoint.
    """

    router = APIRouter()

    @router.get("/web-tool")
    async def web_tool_redirect():
        """Redirect /web-tool to /web_tool/index.html"""
        return Response(status_code=302, headers={"Location": "/web-tool/index.html"})

    @router.get("/web_tool")
    async def web_tool_redirect_alt():
        """Redirect /web_tool to /web_tool/index.html"""
        return Response(status_code=302, headers={"Location": "/web-tool/index.html"})

    @router.get("/live2d-models/info")
    async def get_live2d_folder_info():
        """Get information about available Live2D models"""
        live2d_dir = "live2d-models"
        if not os.path.exists(live2d_dir):
            return JSONResponse(
                {"error": "Live2D models directory not found"}, status_code=404
            )

        valid_characters = []
        supported_extensions = [".png", ".jpg", ".jpeg"]

        for entry in os.scandir(live2d_dir):
            if entry.is_dir():
                folder_name = entry.name.replace("\\", "/")
                model3_file = os.path.join(
                    live2d_dir, folder_name, f"{folder_name}.model3.json"
                ).replace("\\", "/")

                if os.path.isfile(model3_file):
                    # Find avatar file if it exists
                    avatar_file = None
                    for ext in supported_extensions:
                        avatar_path = os.path.join(
                            live2d_dir, folder_name, f"{folder_name}{ext}"
                        )
                        if os.path.isfile(avatar_path):
                            avatar_file = avatar_path.replace("\\", "/")
                            break

                    valid_characters.append(
                        {
                            "name": folder_name,
                            "avatar": avatar_file,
                            "model_path": model3_file,
                        }
                    )
        return JSONResponse(
            {
                "type": "live2d-models/info",
                "count": len(valid_characters),
                "characters": valid_characters,
            }
        )

    @router.post("/asr")
    async def transcribe_audio(file: UploadFile = File(...)):
        """
        Endpoint for transcribing audio using the ASR engine
        """
        logger.info(f"Received audio file for transcription: {file.filename}")

        try:
            # Validate file upload (filename, content type, size)
            file_size = 0
            contents = b""
            async for chunk in file.stream():
                file_size += len(chunk)
                contents += chunk
                # Check size during streaming to prevent memory exhaustion
                if file_size > 10 * 1024 * 1024:  # 10MB limit
                    raise ValueError("File too large. Maximum size: 10MB")
            
            # Validate upload using schema
            validate_file_upload(
                filename=file.filename or "unknown",
                content_type=file.content_type,
                file_size=file_size,
            )

            # Validate minimum file size
            if len(contents) < 44:  # Minimum WAV header size
                raise ValueError("Invalid WAV file: File too small")

            # Decode the WAV header and get actual audio data
            wav_header_size = 44  # Standard WAV header size
            audio_data = contents[wav_header_size:]

            # Validate audio data size
            if len(audio_data) % 2 != 0:
                raise ValueError("Invalid audio data: Buffer size must be even")

            # Convert to 16-bit PCM samples to float32
            try:
                audio_array = (
                    np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )
            except ValueError as e:
                raise ValueError(
                    f"Audio format error: {str(e)}. Please ensure the file is 16-bit PCM WAV format."
                )

            # Validate audio data
            if len(audio_array) == 0:
                raise ValueError("Empty audio data")

            text = await default_context_cache.asr_engine.async_transcribe_np(
                audio_array
            )
            logger.info(f"Transcription result: {text}")
            return {"text": text}

        except ValueError as e:
            logger.error(f"Audio format error: {e}")
            return Response(
                content=json.dumps({"error": str(e)}),
                status_code=400,
                media_type="application/json",
            )
        except Exception as e:
            logger.error(f"Error during transcription: {e}")
            return Response(
                content=json.dumps(
                    {"error": "Internal server error during transcription"}
                ),
                status_code=500,
                media_type="application/json",
            )

    @router.websocket("/tts-ws")
    async def tts_endpoint(websocket: WebSocket):
        """WebSocket endpoint for TTS generation"""
        await websocket.accept()
        
        # Apply rate limiting after accepting connection
        try:
            await check_websocket_rate_limit(websocket)
        except WebSocketDisconnect:
            return  # Connection closed due to rate limit
        logger.info("TTS WebSocket connection established")

        try:
            while True:
                data = await websocket.receive_json()
                
                # Validate input
                from .security.input_validation import validate_websocket_message
                validated_data = validate_websocket_message(data)
                
                text = validated_data.get("text")
                if not text:
                    continue

                logger.info(f"Received text for TTS: {text}")

                # Split text into sentences (with validation)
                sentences = [s.strip() for s in text.split(".") if s.strip()]
                # Limit number of sentences to prevent abuse
                if len(sentences) > 100:
                    sentences = sentences[:100]
                    logger.warning("Sentence count limited to 100")

                try:
                    # Generate and send audio for each sentence
                    for sentence in sentences:
                        sentence = sentence + "."  # Add back the period
                        file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid4())[:8]}"
                        audio_path = (
                            await default_context_cache.tts_engine.async_generate_audio(
                                text=sentence, file_name_no_ext=file_name
                            )
                        )
                        logger.info(
                            f"Generated audio for sentence: {sentence} at: {audio_path}"
                        )

                        await websocket.send_json(
                            {
                                "status": "partial",
                                "audioPath": audio_path,
                                "text": sentence,
                            }
                        )

                    # Send completion signal
                    await websocket.send_json({"status": "complete"})

                except Exception as e:
                    logger.error(f"Error generating TTS: {e}")
                    await websocket.send_json({"status": "error", "message": str(e)})

        except WebSocketDisconnect:
            logger.info("TTS WebSocket client disconnected")
        except Exception as e:
            logger.error(f"Error in TTS WebSocket connection: {e}")
            await websocket.close()

    return router

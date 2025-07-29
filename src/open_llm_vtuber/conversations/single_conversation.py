# src/open_llm_vtuber/conversations/single_conversation.py

from typing import Union, List, Dict, Any, Optional
import asyncio
import json
import re
from loguru import logger
import numpy as np

from .conversation_utils import (
    create_batch_input,
    send_conversation_start_signals,
    process_user_input,
    finalize_conversation_turn,
    cleanup_conversation,
    EMOJI_LIST,
)
from .types import WebSocketSend
from .tts_manager import TTSTaskManager
from ..chat_history_manager import store_message
from ..service_context import ServiceContext

# Import necessary types from agent outputs
from ..agent.output_types import SentenceOutput, AudioOutput, DisplayText


def clean_response(text: str) -> str:
    """
    1) Remove bracketed annotations [like this] (and any surrounding spaces)
    2) Ensure there's a space after punctuation (.,!?;:) if missing
    3) Collapse multiple spaces into one and trim
    """
    # 1) strip out [annotations]
    text = re.sub(r"\s*\[.*?\]\s*", " ", text)
    # 2) ensure a space after punctuation
    text = re.sub(r"([,\.!?;:])([^\s])", r"\1 \2", text)
    # 3) collapse whitespace
    return re.sub(r"\s+", " ", text).strip()


async def process_single_conversation(
    context: ServiceContext,
    websocket_send: WebSocketSend,
    client_uid: str,
    user_input: Union[str, np.ndarray],
    images: Optional[List[Dict[str, Any]]] = None,
    session_emoji: str = np.random.choice(EMOJI_LIST),
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Process a single-user conversation turn end-to-end, with one-shot TTS."""
    tts_manager = TTSTaskManager()
    full_response = ""

    try:
        # 1) Kick things off
        await send_conversation_start_signals(websocket_send)
        logger.info(f"New Conversation Chain {session_emoji} started!")

        # 2) Handle ASR or pass-thru text
        input_text = await process_user_input(
            user_input, context.asr_engine, websocket_send
        )

        # 3) Prepare LLM input, store human message
        batch_input = create_batch_input(
            input_text=input_text,
            images=images,
            from_name=context.character_config.human_name,
            metadata=metadata,
        )
        skip_history = metadata and metadata.get("skip_history", False)
        if context.history_uid and not skip_history:
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="human",
                content=input_text,
                name=context.character_config.human_name,
            )

        logger.info(f"User input: {input_text}")

        # 4) Stream the LLM and buffer all its text
        try:
            agent_stream = context.agent_engine.chat(batch_input)
            async for output_item in agent_stream:
                # a) tool-status events
                if isinstance(output_item, dict) and output_item.get("type") == "tool_call_status":
                    output_item["name"] = context.character_config.character_name
                    await websocket_send(json.dumps(output_item))

                # b) SentenceOutput: extract and buffer every tts_text
                elif isinstance(output_item, SentenceOutput):
                    async for disp, tts_text, actions in output_item:
                        full_response += tts_text

                # c) AudioOutput: if any transcripts come along
                elif isinstance(output_item, AudioOutput):
                    async for _, _, transcript, _ in output_item:
                        full_response += transcript

                else:
                    logger.warning(f"Unexpected output type: {type(output_item)}")

        except Exception as e:
            logger.exception(f"Error collecting LLM response: {e}")
            await websocket_send(json.dumps({"type": "error", "message": str(e)}))

        # 5) Clean and normalize spacing
        cleaned_response = clean_response(full_response)
        logger.debug(f"Cleaned response for TTS: {cleaned_response!r}")

        # 6) One‐shot TTS on the cleaned response
        if cleaned_response:
            display = DisplayText(
                text=cleaned_response,
                name=context.character_config.character_name,
                avatar=context.character_config.avatar,
            )
            await tts_manager.speak(
                tts_text=cleaned_response,
                display_text=display,
                actions=None,
                live2d_model=context.live2d_model,
                tts_engine=context.tts_engine,
                websocket_send=websocket_send,
            )

        # 7) Wait for TTS to finish
        if tts_manager.task_list:
            await asyncio.gather(*tts_manager.task_list)
            await websocket_send(json.dumps({"type": "backend-synth-complete"}))

        # 8) Signal end of turn
        await finalize_conversation_turn(
            tts_manager=tts_manager,
            websocket_send=websocket_send,
            client_uid=client_uid,
        )

        # 9) Store the cleaned AI response in history
        if context.history_uid and cleaned_response:
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="ai",
                content=cleaned_response,
                name=context.character_config.character_name,
                avatar=context.character_config.avatar,
            )
            logger.info(f"AI response: {cleaned_response}")

        return cleaned_response

    except asyncio.CancelledError:
        logger.info(f"Conversation {session_emoji} cancelled.")
        raise

    finally:
        cleanup_conversation(tts_manager, session_emoji)

"""Description: This file contains the implementation of the `AsyncLLM` class.
This class is responsible for handling asynchronous interaction with OpenAI API compatible
endpoints for language generation.
"""

from typing import AsyncIterator, List, Dict, Any
from openai import (
    AsyncStream,
    AsyncOpenAI,
    APIError,
    APIConnectionError,
    RateLimitError,
    NotGiven,
    NOT_GIVEN,
)
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall
from loguru import logger

from .stateless_llm_interface import StatelessLLMInterface
from ...mcpp.types import ToolCallObject


class AsyncLLM(StatelessLLMInterface):
    def __init__(
        self,
        model: str,
        base_url: str,
        llm_api_key: str = "z",
        organization_id: str = "z",
        project_id: str = "z",
        temperature: float = 1.0,
    ):
        """
        Initializes an instance of the `AsyncLLM` class.

        Parameters:
        - model (str): The model to be used for language generation.
        - base_url (str): The base URL for the OpenAI API.
        - organization_id (str, optional): The organization ID for the OpenAI API. Defaults to "z".
        - project_id (str, optional): The project ID for the OpenAI API. Defaults to "z".
        - llm_api_key (str, optional): The API key for the OpenAI API. Defaults to "z".
        - temperature (float, optional): What sampling temperature to use, between 0 and 2. Defaults to 1.0.
        """
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.client = AsyncOpenAI(
            base_url=base_url,
            organization=organization_id,
            project=project_id,
            api_key=llm_api_key,
        )
        self.support_tools = True

        logger.info(
            f"Initialized AsyncLLM with the parameters: {self.base_url}, {self.model}"
        )

    async def chat_completion(
    self,
    messages: List[Dict[str, Any]],
    system: str = None,
    tools: List[Dict[str, Any]] | NotGiven = NOT_GIVEN,
) -> AsyncIterator[str | List[ChoiceDeltaToolCall]]:
        """
        Generates a complete chat response (no streaming).
        """

        stream = None
        accumulated_tool_calls = {}
        in_tool_call = False
        final_text = ""

        try:
            # Add system prompt if provided
            messages_with_system = messages
            if system:
                messages_with_system = [
                    {"role": "system", "content": system},
                    *messages,
                ]
            logger.debug(f"Messages: {messages_with_system}")

            available_tools = tools if self.support_tools else NOT_GIVEN

            stream: AsyncStream[
                ChatCompletionChunk
            ] = await self.client.chat.completions.create(
                messages=messages_with_system,
                model=self.model,
                stream=True,
                temperature=self.temperature,
                tools=available_tools,
            )
            logger.debug(
                f"Tool Support: {self.support_tools}, Available tools: {available_tools}"
            )

            async for chunk in stream:
                if self.support_tools:
                    has_tool_calls = (
                        hasattr(chunk.choices[0].delta, "tool_calls")
                        and chunk.choices[0].delta.tool_calls
                    )

                    if has_tool_calls:
                        in_tool_call = True
                        for tool_call in chunk.choices[0].delta.tool_calls:
                            index = getattr(tool_call, "index", 0)
                            if index not in accumulated_tool_calls:
                                accumulated_tool_calls[index] = {
                                    "index": index,
                                    "id": getattr(tool_call, "id", None),
                                    "type": getattr(tool_call, "type", None),
                                    "function": {"name": "", "arguments": ""},
                                }
                            if getattr(tool_call, "id", None):
                                accumulated_tool_calls[index]["id"] = tool_call.id
                            if getattr(tool_call, "type", None):
                                accumulated_tool_calls[index]["type"] = tool_call.type
                            if getattr(tool_call, "function", None):
                                if getattr(tool_call.function, "name", None):
                                    accumulated_tool_calls[index]["function"]["name"] = tool_call.function.name
                                if getattr(tool_call.function, "arguments", None):
                                    accumulated_tool_calls[index]["function"]["arguments"] += tool_call.function.arguments
                        continue

                # Collect normal text content
                if len(chunk.choices) > 0 and chunk.choices[0].delta.content:
                    final_text += chunk.choices[0].delta.content

            # If tool calls exist, yield them instead of text
            if in_tool_call and accumulated_tool_calls:
                complete_tool_calls = [
                    ToolCallObject.from_dict(tool_data)
                    for tool_data in accumulated_tool_calls.values()
                ]
                yield complete_tool_calls
            else:
                print(f"FINAL TEXT: {final_text}")
                yield final_text

        except APIConnectionError as e:
            logger.error(f"Error calling the chat endpoint: {e}")
            yield "Error calling the chat endpoint: Connection error."

        except RateLimitError as e:
            logger.error(f"Error calling the chat endpoint: Rate limit exceeded: {e.response}")
            yield "Error calling the chat endpoint: Rate limit exceeded. Please try again later."

        except APIError as e:
            if "does not support tools" in str(e):
                self.support_tools = False
                yield "__API_NOT_SUPPORT_TOOLS__"
                return
            logger.error(f"LLM API: Error occurred: {e}")
            yield "Error calling the chat endpoint: Error occurred while generating response."

        finally:
            if stream:
                await stream.close()


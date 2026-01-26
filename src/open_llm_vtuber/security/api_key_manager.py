"""
API Key Management
Handles secure loading of API keys from environment variables
Follows OWASP best practices for secret management
"""

import os
from typing import Optional
from loguru import logger


class APIKeyManager:
    """
    Manages API keys securely using environment variables
    Prevents hard-coding of sensitive credentials
    Follows OWASP best practices for secret management
    """

    # Mapping of config keys to environment variable names
    # Format: "config_path": "ENV_VAR_NAME"
    ENV_VAR_MAPPING = {
        # LLM API Keys
        "openai_llm.llm_api_key": "OPENAI_API_KEY",
        "claude_llm.llm_api_key": "ANTHROPIC_API_KEY",
        "gemini_llm.llm_api_key": "GEMINI_API_KEY",
        "deepseek_llm.llm_api_key": "DEEPSEEK_API_KEY",
        "mistral_llm.llm_api_key": "MISTRAL_API_KEY",
        "groq_llm.llm_api_key": "GROQ_API_KEY",
        "zhipu_llm.llm_api_key": "ZHIPU_API_KEY",
        # TTS API Keys
        "elevenlabs_tts.api_key": "ELEVENLABS_API_KEY",
        "azure_tts.api_key": "AZURE_TTS_API_KEY",
        "azure_tts.region": "AZURE_TTS_REGION",
        "cartesia_tts.api_key": "CARTESIA_API_KEY",
        "minimax_tts.api_key": "MINIMAX_API_KEY",
        "siliconflow_tts.api_key": "SILICONFLOW_API_KEY",
        "fish_api_tts.api_key": "FISH_API_KEY",
        # ASR API Keys
        "azure_asr.api_key": "AZURE_ASR_API_KEY",
        "azure_asr.region": "AZURE_ASR_REGION",
        "groq_whisper_asr.api_key": "GROQ_API_KEY",  # Reuses Groq key
        # Agent API Keys
        "hume_ai.api_key": "HUME_AI_API_KEY",
    }

    @staticmethod
    def get_api_key_from_env(
        config_path: str, fallback: Optional[str] = None
    ) -> Optional[str]:
        """
        Get API key from environment variable
        Prioritizes environment variables over config file values

        Args:
            config_path: Config path (e.g., "openai_llm.llm_api_key")
            fallback: Fallback value if env var not set (from config file)

        Returns:
            API key from environment or fallback, or None if neither available
        """
        env_var = APIKeyManager.ENV_VAR_MAPPING.get(config_path)
        if env_var:
            api_key = os.getenv(env_var)
            if api_key:
                logger.debug(
                    f"Loaded API key for {config_path} from environment variable {env_var}"
                )
                return api_key
            elif fallback:
                # Check if fallback looks like a placeholder
                placeholder_values = [
                    "YOUR API KEY HERE",
                    "Your Open AI API key",
                    "Your API key",
                    "somethingelse",
                    "default_api_key",
                    "not-needed",
                    "z",
                ]
                if fallback not in placeholder_values:
                    logger.warning(
                        f"Environment variable {env_var} not set for {config_path}, "
                        f"using value from config file. Consider moving to environment variable for security."
                    )
                return fallback
            else:
                logger.warning(
                    f"Environment variable {env_var} not set for {config_path} and no fallback provided"
                )
                return None
        else:
            # No environment variable mapping, use fallback
            if fallback:
                logger.debug(f"Using fallback API key for {config_path}")
                return fallback
            return None

    @staticmethod
    def mask_api_key(api_key: Optional[str]) -> str:
        """
        Mask API key for logging (show only first 4 and last 4 characters)
        Prevents accidental exposure in logs

        Args:
            api_key: API key to mask

        Returns:
            Masked API key string
        """
        if not api_key:
            return "None"
        if len(api_key) <= 8:
            return "****"
        return f"{api_key[:4]}...{api_key[-4:]}"

    @staticmethod
    def validate_api_key_not_exposed(config_dict: dict) -> list:
        """
        Security audit: Check if API keys are hard-coded in config
        Returns warnings about potentially exposed API keys

        Args:
            config_dict: Configuration dictionary

        Returns:
            List of warnings about potentially exposed API keys
        """
        warnings = []
        sensitive_patterns = [
            "api_key",
            "apikey",
            "api-key",
            "secret",
            "token",
            "password",
            "credential",
        ]

        placeholder_values = [
            "YOUR API KEY HERE",
            "Your Open AI API key",
            "Your API key",
            "somethingelse",
            "default_api_key",
            "not-needed",
            "z",
        ]

        def check_dict(d: dict, path: str = ""):
            for key, value in d.items():
                current_path = f"{path}.{key}" if path else key
                if isinstance(value, dict):
                    check_dict(value, current_path)
                elif isinstance(value, str):
                    key_lower = key.lower()
                    if any(pattern in key_lower for pattern in sensitive_patterns):
                        # Check if it looks like a placeholder or real key
                        if value and value not in placeholder_values:
                            # Check if it's a real-looking key (has reasonable length and complexity)
                            if len(value) > 10 and not value.startswith("http"):
                                warnings.append(
                                    f"Potential hard-coded API key found at {current_path}: "
                                    f"{APIKeyManager.mask_api_key(value)}. "
                                    f"Consider using environment variable instead."
                                )

        check_dict(config_dict)
        return warnings


def get_api_key_from_env(
    config_path: str, fallback: Optional[str] = None
) -> Optional[str]:
    """
    Convenience function to get API key from environment

    Args:
        config_path: Config path (e.g., "openai_llm.llm_api_key")
        fallback: Fallback value if env var not set

    Returns:
        API key from environment or fallback
    """
    return APIKeyManager.get_api_key_from_env(config_path, fallback)

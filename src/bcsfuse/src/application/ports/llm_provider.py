from typing import Protocol, List, Optional, AsyncIterator


class LLMProvider(Protocol):
    """Public LLM provider contract.

    Implementations may be OSS defaults (OpenAI, Anthropic) or internal plugins.
    Public code must depend on this contract, not internal LLM SDKs.
    """

    def complete(self, prompt: str, **kwargs) -> str:
        """Generate completion for prompt.

        Args:
            prompt: Input prompt
            **kwargs: Additional provider-specific options

        Returns:
            Generated completion text.
        """
        ...

    async def complete_async(self, prompt: str, **kwargs) -> str:
        """Generate completion for prompt (async).

        Args:
            prompt: Input prompt
            **kwargs: Additional provider-specific options

        Returns:
            Generated completion text.
        """
        ...

    async def stream(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        """Stream completion for prompt.

        Args:
            prompt: Input prompt
            **kwargs: Additional provider-specific options

        Yields:
            Completion chunks.
        """
        ...

    def chat(self, messages: List[dict], **kwargs) -> str:
        """Generate chat completion.

        Args:
            messages: List of message dicts with 'role' and 'content'
            **kwargs: Additional provider-specific options

        Returns:
            Generated response text.
        """
        ...

    async def chat_async(self, messages: List[dict], **kwargs) -> str:
        """Generate chat completion (async).

        Args:
            messages: List of message dicts with 'role' and 'content'
            **kwargs: Additional provider-specific options

        Returns:
            Generated response text.
        """
        ...
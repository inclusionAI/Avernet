from typing import Protocol, List


class EmbeddingProvider(Protocol):
    """Public embedding provider contract.

    Implementations may be OSS defaults (OpenAI, Sentence Transformers) or internal plugins.
    Public code must depend on this contract, not internal embedding SDKs.
    """

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for texts.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors.
        """
        ...

    def embed_single(self, text: str) -> List[float]:
        """Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector.
        """
        ...

    def get_dimension(self) -> int:
        """Get embedding dimension.

        Returns:
            Dimension of embedding vectors.
        """
        ...
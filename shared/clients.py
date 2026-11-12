"""Factories for external Azure service clients."""

import os
from functools import lru_cache

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.projects import AIProjectClient
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential


def di() -> DocumentIntelligenceClient:
    return DocumentIntelligenceClient(
        os.environ["DI_ENDPOINT"],
        AzureKeyCredential(os.environ["DI_KEY"]),
    )


@lru_cache(maxsize=1)
def foundry_openai_client():
    """Create a cached Foundry OpenAI client using the current Azure identity."""
    project = AIProjectClient(
        endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        credential=DefaultAzureCredential(),
    )
    return project.get_openai_client()

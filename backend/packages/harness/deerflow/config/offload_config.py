"""Configuration for context offload middleware."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OffloadConfig(BaseModel):
    """Configuration for context offload middleware.

    When the message token count reaches *threshold*, the middleware packages the
    conversation context (messages, goal, delegations, skill_context) and writes
    it to disk under *offload_dir*, then trims state messages to *messages_to_keep*.
    """

    enabled: bool = Field(
        default=True,
        description="Enable context offloading when token count reaches threshold.",
    )
    threshold: int = Field(
        default=150_000,
        ge=1,
        description="Token threshold that triggers context offload.",
    )
    messages_to_keep: int = Field(
        default=10,
        ge=1,
        description="Number of most recent messages to keep after offloading.",
    )
    offload_dir: str = Field(
        default=".fat/threads",
        description="Base directory for offload files, relative to CWD.",
    )

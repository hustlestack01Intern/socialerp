"""
Conversation Package

Unified inbox, conversation state management, and human handoff.
"""

from packages.conversation.agent_service import (
    ConversationAgentResult,
    ConversationAgentService,
    MessageSender,
    autonomy_from_settings,
)
from packages.conversation.handoff import HandoffManager
from packages.conversation.manager import ConversationManager

__all__ = [
    "ConversationAgentResult",
    "ConversationAgentService",
    "ConversationManager",
    "HandoffManager",
    "MessageSender",
    "autonomy_from_settings",
]

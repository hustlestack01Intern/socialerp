"""
Conversation Package

Unified inbox, conversation state management, and human handoff.
"""

from packages.conversation.handoff import HandoffManager
from packages.conversation.manager import ConversationManager

__all__ = ["ConversationManager", "HandoffManager"]

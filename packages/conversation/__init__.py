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
from packages.conversation.rider_flow import (
    RiderCommand,
    RiderFlowResult,
    RiderFlowService,
    parse_rider_command,
)

__all__ = [
    "ConversationAgentResult",
    "ConversationAgentService",
    "ConversationManager",
    "HandoffManager",
    "MessageSender",
    "RiderCommand",
    "RiderFlowResult",
    "RiderFlowService",
    "autonomy_from_settings",
    "parse_rider_command",
]

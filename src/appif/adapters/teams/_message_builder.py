"""Build Graph request payloads + routing for outbound Teams messages.

Translates an outbound ``MessageContent`` + ``ConversationRef`` into the
Graph endpoint path and JSON body for sending a chat message, a new channel
message, or a reply to a channel message.
"""

from __future__ import annotations

from appif.domain.messaging.errors import NotSupported
from appif.domain.messaging.models import ConversationRef, MessageContent

_CONNECTOR_NAME = "teams"


def build_message(conversation: ConversationRef, content: MessageContent) -> tuple[str, dict]:
    """Return ``(path, body)`` for the Graph POST that sends ``content``.

    ``path`` is the URL path appended to the Graph v1.0 base. Routing:

    - chat                 -> ``/chats/{chat_id}/messages``
    - new channel message  -> ``/teams/{team_id}/channels/{channel_id}/messages``
    - channel reply        -> ``/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies``

    The reply form is used for channel conversations whenever the
    ``ConversationRef`` carries a root ``message_id``.
    """
    opaque = conversation.opaque_id
    body = {"body": {"contentType": "text", "content": content.text}}

    if conversation.type == "chat":
        chat_id = opaque.get("chat_id")
        if not chat_id:
            raise NotSupported(_CONNECTOR_NAME, operation="send to chat without chat_id")
        return f"/chats/{chat_id}/messages", body

    if conversation.type == "channel":
        team_id = opaque.get("team_id")
        channel_id = opaque.get("channel_id")
        if not (team_id and channel_id):
            raise NotSupported(_CONNECTOR_NAME, operation="send to channel without team_id/channel_id")
        message_id = opaque.get("message_id")
        if message_id:
            return f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies", body
        return f"/teams/{team_id}/channels/{channel_id}/messages", body

    raise NotSupported(_CONNECTOR_NAME, operation=f"send to conversation type '{conversation.type}'")

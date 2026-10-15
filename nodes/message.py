from langgraph.graph.message import RemoveMessage
from state import K12State

def remove_message(state: K12State):

    messages = state['messages']
    if (len(messages) >= 10):
        messages = [RemoveMessage(id=m.id) for m in state['messages'][:-3]]

    return {'messages': messages}
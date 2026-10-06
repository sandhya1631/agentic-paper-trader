"""Agent Layer — reasoning, tool-call validation, and (later) the scheduled decision loop.

Per the architecture doc: build context -> call model -> validate proposal -> send to
policy engine. This package never calls the broker directly.
"""

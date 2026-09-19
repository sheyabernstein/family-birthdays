from enum import StrEnum


class TaskPriority(StrEnum):
    """The Celery queue name a task dispatches on, named by urgency tier.

    Not notifications-specific - any app dispatching a Celery task can use
    this (accounts' magic-link task included), so it lives here rather
    than under one app that happens to use it most.

    This is a named-queue scheme, not Redis's own per-message priority
    (`Task.apply_async`'s `priority=` kwarg / kombu's `priority_steps`) -
    deliberately. Redis has no native priority concept; kombu's redis
    transport emulates one by splitting a single queue into several
    priority-suffixed sub-keys, and this app hit a live kombu 5.6.2 bug
    doing exactly that: the moment any task actually carried a non-zero
    priority, the worker's own pidbox control-command replies (mingle/
    heartbeat traffic, always priority 0) started throwing
    `ValueError: not enough values to unpack` from kombu's exchange
    lookup, and - worse than just a logged error - the worker silently
    stopped consuming the priority-suffixed queue afterward, needing a
    restart to drain it. Reproduced live against a real docker stack:
    a HIGH-priority magic-link task queued after the bug fires just sits
    in Redis, unconsumed, until the worker restarts.

    The fix is three separate real Celery queues (`high`/`normal`/`low`,
    one per member's value here) consumed by the same single worker
    process, in that order, via `queue_order_strategy: "priority"` in
    CELERY_BROKER_TRANSPORT_OPTIONS (config/settings.py) - kombu's docs
    describe this mode plainly: "Consume from queues in original order,
    so that if the first queue always contains messages, the rest of the
    queues in the list will never be consumed from." That's real,
    deterministic priority ordering, and - critically - it never sets a
    per-message Redis priority at all, so it never touches the buggy
    code path above. No dedicated per-tier worker process is needed
    either - see docker/entrypoints/worker.sh's `-Q high,normal,low`.
    """

    HIGH = "high"  # a human is waiting right now (e.g. a magic-link sign-in)
    NORMAL = "normal"  # an actual message send
    LOW = "low"  # scheduled/background work (nightly sweeps, dispatchers)

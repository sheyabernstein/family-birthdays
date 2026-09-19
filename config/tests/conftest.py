def sample_value(metric, **labels) -> float:
    """Reads a labeled Prometheus metric's current value - shared by every
    config/observability test that asserts on a counter/gauge before and
    after some action."""
    return metric.labels(**labels)._value.get()

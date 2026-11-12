import time


def stage_a():
    time.sleep(0.12)
    return "a"


def separator():
    return "-" * 3


def joiner():
    return "=" * 3


def stage_b():
    time.sleep(0.08)
    return "b"


def run():
    return stage_a() + separator() + joiner() + stage_b()

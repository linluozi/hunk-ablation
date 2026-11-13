import time

FLAG_A = False


def stage():
    time.sleep(0.2)
    return "x"


FLAG_B = False


def run():
    # Neither flag alone skips the sleep; both together do.  The two hunks in
    # patch.diff are therefore perfectly synergistic: each alone saves nothing.
    if not (FLAG_A and FLAG_B):
        return stage()
    return "x"

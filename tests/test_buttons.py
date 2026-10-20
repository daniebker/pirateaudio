from pirateplayer.buttons import COMBO, COMBO_TIME, HOLD, HOLD_TIME, REPEAT, REPEAT_TIME, TAP, PressTracker

UP = {"A": False, "B": False, "X": False, "Y": False}


def press(**down):
    return UP | down


def test_tap():
    t = PressTracker()
    assert t.update(press(X=True), 0.0) == []
    assert t.update(press(X=True), 0.1) == []
    assert t.update(UP, 0.2) == [("X", TAP)]
    assert t.update(UP, 0.3) == []


def test_hold_then_repeat_and_no_tap_on_release():
    t = PressTracker()
    t.update(press(A=True), 0.0)
    assert t.update(press(A=True), HOLD_TIME) == [("A", HOLD)]
    assert t.update(press(A=True), HOLD_TIME + REPEAT_TIME / 2) == []
    assert t.update(press(A=True), HOLD_TIME + REPEAT_TIME + 0.01) == [("A", REPEAT)]
    assert t.update(press(A=True), HOLD_TIME + 2 * REPEAT_TIME + 0.02) == [("A", REPEAT)]
    assert t.update(UP, 5.0) == []


def test_buttons_are_independent():
    t = PressTracker()
    t.update(press(X=True), 0.0)
    t.update(press(X=True, Y=True), 0.1)
    assert t.update(press(Y=True), 0.2) == [("X", TAP)]
    assert t.update(UP, 0.3) == [("Y", TAP)]


def test_combo_fires_once_and_suppresses_a_and_b():
    t = PressTracker()
    events = []
    now = 0.0
    while now <= COMBO_TIME + 1:
        events += t.update(press(A=True, B=True), now)
        now += 0.05
    assert events == [(COMBO, HOLD)]
    assert t.update(press(A=True), now) == []
    assert t.update(UP, now + 0.1) == []
    # Afterwards, buttons work normally again.
    t.update(press(A=True), now + 0.2)
    assert t.update(UP, now + 0.3) == [("A", TAP)]


def test_released_combo_before_time_does_not_fire():
    t = PressTracker()
    t.update(press(A=True, B=True), 0.0)
    t.update(press(A=True, B=True), COMBO_TIME - 0.5)
    assert t.update(UP, COMBO_TIME - 0.4) == []
    t.update(press(A=True, B=True), COMBO_TIME)
    assert t.update(press(A=True, B=True), COMBO_TIME + 0.1) == []

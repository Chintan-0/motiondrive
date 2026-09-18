import pytest

from motiondrive.calibration import CalibrationWizard, CalibrationStep


def test_full_calibration_flow_success():
    wiz = CalibrationWizard()
    assert wiz.step == CalibrationStep.INTRO

    wiz.capture()  # intro -> center
    assert wiz.step == CalibrationStep.CENTER

    for _ in range(10):
        wiz.feed_sample(0.0, 0.35)
    wiz.capture()  # center -> rotate left
    assert wiz.step == CalibrationStep.ROTATE_LEFT
    assert wiz.result.center_angle_deg == 0.0

    for _ in range(10):
        wiz.feed_sample(-35.0, 0.35)
    err = wiz.capture()  # rotate left -> rotate right
    assert err is None
    assert wiz.step == CalibrationStep.ROTATE_RIGHT
    assert wiz.result.left_angle_deg == -35.0

    for _ in range(10):
        wiz.feed_sample(35.0, 0.35)
    err = wiz.capture()  # rotate right -> right pinch open
    assert err is None
    assert wiz.step == CalibrationStep.RIGHT_PINCH_OPEN
    assert wiz.result.right_angle_deg == 35.0
    assert wiz.result.calibrated is True

    for _ in range(10):
        wiz.feed_pinch_sample(0.1)
    wiz.capture()  # -> right pinch closed
    assert wiz.step == CalibrationStep.RIGHT_PINCH_CLOSED
    assert wiz.result.right_pinch_open == pytest.approx(0.1)

    for _ in range(10):
        wiz.feed_pinch_sample(0.9)
    err = wiz.capture()  # -> left pinch open
    assert err is None
    assert wiz.step == CalibrationStep.LEFT_PINCH_OPEN
    assert wiz.result.right_pinch_closed == pytest.approx(0.9)

    for _ in range(10):
        wiz.feed_pinch_sample(0.1)
    wiz.capture()  # -> left pinch closed
    assert wiz.step == CalibrationStep.LEFT_PINCH_CLOSED

    for _ in range(10):
        wiz.feed_pinch_sample(0.9)
    err = wiz.capture()  # -> done
    assert err is None
    assert wiz.is_done()
    assert wiz.result.left_pinch_closed == pytest.approx(0.9)
    assert wiz.result.pedals_calibrated is True


def test_calibration_rejects_insufficient_pinch_depth():
    wiz = CalibrationWizard()
    wiz.capture()
    for _ in range(3):
        wiz.feed_sample(0.0, 0.35)
    wiz.capture()
    for _ in range(3):
        wiz.feed_sample(-35.0, 0.35)
    wiz.capture()
    for _ in range(3):
        wiz.feed_sample(35.0, 0.35)
    wiz.capture()  # -> right pinch open
    for _ in range(5):
        wiz.feed_pinch_sample(0.1)
    wiz.capture()  # -> right pinch closed
    for _ in range(5):
        wiz.feed_pinch_sample(0.15)  # barely pinched
    err = wiz.capture()
    assert err is not None
    assert wiz.step == CalibrationStep.RIGHT_PINCH_CLOSED  # stayed on same step


def test_calibration_rejects_insufficient_left_rotation():
    wiz = CalibrationWizard()
    wiz.capture()  # -> center
    for _ in range(5):
        wiz.feed_sample(0.0, 0.35)
    wiz.capture()  # -> rotate left
    for _ in range(5):
        wiz.feed_sample(2.0, 0.35)  # barely moved
    err = wiz.capture()
    assert err is not None
    assert wiz.step == CalibrationStep.ROTATE_LEFT  # stayed on same step


def test_calibration_rejects_right_turn_in_same_direction_as_left():
    wiz = CalibrationWizard()
    wiz.capture()
    for _ in range(5):
        wiz.feed_sample(0.0, 0.35)
    wiz.capture()
    for _ in range(5):
        wiz.feed_sample(-35.0, 0.35)
    wiz.capture()
    for _ in range(5):
        wiz.feed_sample(-40.0, 0.35)  # also went left, not right
    err = wiz.capture()
    assert err is not None
    assert wiz.step == CalibrationStep.ROTATE_RIGHT


def test_calibration_store_roundtrip(tmp_path):
    from motiondrive.calibration import CalibrationStore, Calibration
    store = CalibrationStore(path=tmp_path / "cal.json")
    cal = Calibration(center_angle_deg=1.5, left_angle_deg=-40.0, right_angle_deg=42.0,
                       hand_distance_baseline=0.4, calibrated=True)
    store.save(cal)
    loaded = store.load()
    assert loaded == cal


def test_calibration_store_missing_file_returns_default(tmp_path):
    from motiondrive.calibration import CalibrationStore, Calibration
    store = CalibrationStore(path=tmp_path / "nope.json")
    loaded = store.load()
    assert loaded == Calibration.default()

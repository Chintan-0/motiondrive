from motiondrive.windows_input import VK_CODES, send_virtual_key


def test_vk_codes_cover_the_keys_motiondrive_ever_sends():
    for key in ("a", "d", "w", "s", "space", "left", "right", "up", "down"):
        assert key in VK_CODES


def test_send_virtual_key_returns_false_for_unknown_key_without_calling_sendinput():
    # No mapped VK code -- must short-circuit to False, never attempt a
    # Win32 call with a bogus/zero code.
    assert send_virtual_key("not-a-real-key", True) is False

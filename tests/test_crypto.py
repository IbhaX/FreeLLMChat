from app.crypto import decrypt_key, encrypt_key, key_hint


def test_encrypt_decrypt_roundtrip():
    ct = encrypt_key("sk-super-secret-1234")
    assert ct != "sk-super-secret-1234"
    assert decrypt_key(ct) == "sk-super-secret-1234"


def test_key_hint():
    assert key_hint("sk-abcdefgh") == "efgh"
    assert key_hint("ab") == "****"

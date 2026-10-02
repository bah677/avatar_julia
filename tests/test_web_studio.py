"""Веб-студия: разбор id объектов, нарезка сырья под выжимку, обрезка контекста."""

from __future__ import annotations

import json

from web.objects import parse_object_id, parse_object_ids, source_label
from web.pipeline import _fallback_plan, _trim, format_excerpts, split_for_distill
from web.prompts import context_block


def test_parse_object_id_kinds():
    assert parse_object_id("lesson:2.4").kind == "lesson"
    assert parse_object_id("passport:expert").value == "expert"
    assert parse_object_id("live:testimonial").kind == "live"
    sid = "11111111-2222-3333-4444-555555555555"
    assert parse_object_id(f"source:{sid}").value == sid


def test_parse_object_id_rejects_garbage():
    assert parse_object_id("") is None
    assert parse_object_id("lesson") is None
    assert parse_object_id("source:not-a-uuid") is None
    assert parse_object_id("passport:unknown") is None
    assert parse_object_id("live:whatever") is None


def test_parse_object_ids_dedupes_and_keeps_order():
    refs = parse_object_ids(["lesson:2.4", "lesson:2.4", "passport:launch", "мусор"])
    assert [r.id for r in refs] == ["lesson:2.4", "passport:launch"]


def test_source_label_has_kind_and_date():
    import datetime

    label = source_label(
        {
            "kind": "practice",
            "title": "Разбор кейсов",
            "recorded_on": datetime.date(2026, 9, 18),
        }
    )
    assert "Практика" in label
    assert "18.09.2026" in label


def test_split_for_distill_keeps_blocks_and_limits_count():
    text = "\n\n".join(f"блок {i} " + "x" * 400 for i in range(30))
    chunks = split_for_distill(text, chunk_chars=1000, max_chunks=5)
    assert len(chunks) == 5
    # блоки не разрезаны посередине
    assert all(c.startswith("блок ") for c in chunks)


def test_split_for_distill_small_text_single_chunk():
    chunks = split_for_distill("короткий текст", chunk_chars=1000, max_chunks=5)
    assert chunks == ["короткий текст"]


def test_trim_drops_cards_first_and_keeps_raw():
    parts = _trim(
        {
            "raw_full": "r" * 100,
            "excerpts": "e" * 100,
            "rag_chunks": "c" * 100,
            "cards": "k" * 100,
        },
        250,
    )
    assert parts["cards"] == ""
    assert parts["raw_full"] == "r" * 100
    assert sum(len(v) for v in parts.values()) <= 250


def test_fallback_plan_covers_chunks_and_cards():
    """Запасной план — без фильтров: срезы живого чата ищутся отдельно."""
    refs = parse_object_ids(["live:testimonial"])
    searches, focus, _ = _fallback_plan("нужны отзывы", refs)
    assert {s.collection for s in searches} == {"chunks", "cards"}
    assert focus


def test_live_object_ids_and_aliases():
    from web.objects import LIVE_FILTERS, parse_object_id

    assert parse_object_id("live:client").value == "client"
    # старые идентификаторы из сохранённых чатов не теряются
    assert parse_object_id("live:dialog").value == "chat"
    assert parse_object_id("live:expert_reply").value == "expert"
    assert parse_object_id("live:нет-такого") is None
    assert LIVE_FILTERS["client"] == {"origin": "telegram_group", "role": "client"}
    assert LIVE_FILTERS["testimonial"] == {"source_kind": "testimonial"}


def test_format_excerpts_marks_speaker_and_timecode():
    out = format_excerpts(
        {
            "s1": {
                "excerpts": [
                    {"quote": "я поняла, что боюсь просить денег", "start_sec": 125, "speaker": "participant", "why": "боль"}
                ],
                "themes": ["деньги"],
                "audience_voice": ["мне страшно поднимать цену"],
            }
        },
        {"s1": "Практика · 18.09.2026"},
    )
    assert "Практика · 18.09.2026" in out
    assert "участница" in out
    assert "2:05" in out
    assert "мне страшно поднимать цену" in out


def test_context_block_order_and_sections():
    block = context_block(
        focus="запуск 1 октября",
        stage_id="launch",
        objects_summary="- Практика",
        notes="не обещать результат",
        excerpts="выдержки",
        rag_chunks="чанки",
        cards="карточки",
        lesson_passports="паспорт урока",
        raw_full="сырьё",
    )
    assert block.index("## Фокус") < block.index("## Этап")
    assert block.index("## Полная расшифровка выбранного материала") < block.index("## Фрагменты из базы")
    for part in ("запуск 1 октября", "Активные продажи", "не обещать результат", "сырьё", "карточки"):
        assert part in block


def test_web_chat_context_roundtrip_json():
    """Контекст чата хранится как JSON — проверяем, что наши id сериализуются."""
    refs = parse_object_ids(["lesson:1.6", "passport:product"])
    payload = {"objects": [r.id for r in refs]}
    assert json.loads(json.dumps(payload)) == payload


def _set_secret(value: str) -> None:
    """AppConfig — frozen dataclass, поэтому пишем через object.__setattr__."""
    from config import config

    object.__setattr__(config, "WEB_SECRET", value)
    object.__setattr__(config, "WEB_AUTH_TOKEN", "")


def test_admin_ref_is_stable_and_not_the_telegram_id():
    """Ссылка админа — HMAC: одинаковая между запросами и не равна user_id."""
    from web import auth

    _set_secret("secret-for-test")
    ref1 = auth.admin_ref(304631563)
    assert ref1 == auth.admin_ref(304631563)
    assert "304631563" not in ref1
    assert ref1 != auth.admin_ref(304631564)
    assert len(ref1) == 20


def test_hash_code_depends_on_user_secret_and_code():
    from web import auth

    _set_secret("secret-for-test")
    assert auth.hash_code(1, "123456") != auth.hash_code(2, "123456")
    assert auth.hash_code(1, "123456") != auth.hash_code(1, "123457")
    same = auth.hash_code(1, "123456")
    _set_secret("another-secret")
    assert auth.hash_code(1, "123456") != same


def test_display_name_variants():
    from web.auth import display_name

    assert display_name({"first_name": "Юлия", "last_name": "Бахарева"}, 1) == "Юлия Б."
    assert display_name({"first_name": "Юлия"}, 1) == "Юлия"
    assert display_name({"username": "@julia"}, 1) == "@julia"
    assert display_name({"last_name": "@service"}, 1) == "service"
    assert display_name(None, 304631563) == "админ 1563"


def test_secret_required_for_refs():
    from web.auth import AuthError, admin_ref

    _set_secret("")
    try:
        admin_ref(1)
    except AuthError as e:
        assert e.status == 503
    else:
        raise AssertionError("без секрета ссылки выдавать нельзя")
    finally:
        _set_secret("secret-for-test")


# ── вход из мини-аппа Telegram ──────────────────────────────────────────────

def _init_data(token: str, user: dict, auth_date: int) -> str:
    """Собирает подписанный initData, как это делает Telegram."""
    import hashlib
    import hmac
    import json
    from urllib.parse import urlencode

    pairs = {"auth_date": str(auth_date), "query_id": "AAE", "user": json.dumps(user, ensure_ascii=False)}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**pairs, "hash": digest})


def _with_bot_token(token: str) -> None:
    from config import config

    object.__setattr__(config, "BIBLIA_BOT_TOKEN", token)


def test_init_data_valid_signature():
    import time

    from web.auth import parse_init_data

    _with_bot_token("123:TEST")
    raw = _init_data("123:TEST", {"id": 777, "first_name": "Юлия"}, int(time.time()))
    data = parse_init_data(raw)
    assert data["user"]["id"] == 777


def test_init_data_rejects_tampered_payload():
    import time

    from web.auth import AuthError, parse_init_data

    _with_bot_token("123:TEST")
    raw = _init_data("123:TEST", {"id": 777}, int(time.time()))
    tampered = raw.replace("777", "778")
    try:
        parse_init_data(tampered)
    except AuthError as e:
        assert e.status == 401
    else:
        raise AssertionError("подменённый user_id не должен проходить")


def test_init_data_rejects_foreign_token():
    import time

    from web.auth import AuthError, parse_init_data

    raw = _init_data("999:OTHER", {"id": 777}, int(time.time()))
    _with_bot_token("123:TEST")
    try:
        parse_init_data(raw)
    except AuthError as e:
        assert e.status == 401
    else:
        raise AssertionError("подпись чужим токеном не должна проходить")


def test_init_data_rejects_stale():
    import time

    from web.auth import AuthError, parse_init_data

    _with_bot_token("123:TEST")
    raw = _init_data("123:TEST", {"id": 777}, int(time.time()) - 48 * 3600)
    try:
        parse_init_data(raw)
    except AuthError as e:
        assert e.status == 401
    else:
        raise AssertionError("старый initData не должен проходить")

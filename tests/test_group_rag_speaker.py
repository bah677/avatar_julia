"""Роль спикера в групповом RAG и доступ в группах при admin-only."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from config import _parse_rag_groups
from bot.access.policies import LicenseWhitelistPolicy, is_groupish_event
from bot.access.types import AccessContext, AccessDecision
from bot.features.rag_group_metadata import (
    infer_speaker_role,
    is_silent_read_chat,
    message_in_rag_groups_scope,
    participant_dump_prefix,
    speaker_text_prefix,
)


class SpeakerRoleTests(unittest.TestCase):
    def test_expert_by_telegram_id(self) -> None:
        self.assertEqual(
            infer_speaker_role(user_id=304631563, expert_ids={304631563}),
            "expert",
        )
        self.assertEqual(
            infer_speaker_role(user_id=111, expert_ids={304631563}),
            "client",
        )

    def test_prefix_labels(self) -> None:
        expert = SimpleNamespace(first_name="Юлия", last_name="", username="j")
        other = SimpleNamespace(first_name="Анна", last_name="К", username="")
        self.assertEqual(
            speaker_text_prefix(role="expert", user=expert, expert_name="Юлия"),
            "[эксперт Юлия]",
        )
        self.assertEqual(
            speaker_text_prefix(role="client", user=other, expert_name="Юлия"),
            "[участник Анна К]",
        )


class LiveChatScopeTests(unittest.TestCase):
    def test_link_c_to_chat_and_topic_6(self) -> None:
        parsed = _parse_rag_groups("3903313717:6")
        self.assertEqual(set(parsed.keys()), {-1003903313717})
        self.assertEqual(parsed[-1003903313717], frozenset({6}))

    def test_only_topic_6_in_scope(self) -> None:
        groups = _parse_rag_groups("-1003903313717:6")
        topic6 = SimpleNamespace(
            chat=SimpleNamespace(id=-1003903313717),
            message_thread_id=6,
        )
        other = SimpleNamespace(
            chat=SimpleNamespace(id=-1003903313717),
            message_thread_id=7,
        )
        no_topic = SimpleNamespace(
            chat=SimpleNamespace(id=-1003903313717),
            message_thread_id=None,
        )
        self.assertTrue(message_in_rag_groups_scope(topic6, groups))
        self.assertFalse(message_in_rag_groups_scope(other, groups))
        self.assertFalse(message_in_rag_groups_scope(no_topic, groups))

    def test_participant_dump_topic_193(self) -> None:
        groups = _parse_rag_groups("-1003945188229:193")
        dump = SimpleNamespace(
            chat=SimpleNamespace(id=-1003945188229),
            message_thread_id=193,
        )
        other = SimpleNamespace(
            chat=SimpleNamespace(id=-1003945188229),
            message_thread_id=1,
        )
        self.assertTrue(message_in_rag_groups_scope(dump, groups))
        self.assertFalse(message_in_rag_groups_scope(other, groups))

    def test_dump_prefix_ignores_expert_sender(self) -> None:
        typed = SimpleNamespace(
            from_user=SimpleNamespace(first_name="Юлия", last_name="", username="j"),
            forward_origin=None,
            forward_from=None,
        )
        self.assertEqual(participant_dump_prefix(typed), "[участник]")
        forwarded = SimpleNamespace(
            from_user=SimpleNamespace(first_name="Юлия", last_name="", username="j"),
            forward_origin=SimpleNamespace(
                sender_user=SimpleNamespace(first_name="Анна", last_name="К", username=""),
                sender_user_name=None,
                sender_chat=None,
            ),
            forward_from=None,
        )
        self.assertEqual(participant_dump_prefix(forwarded), "[участник Анна К]")

    def test_silent_read_blocks_live_group_entirely(self) -> None:
        live_map = {-1003903313717: frozenset({6})}
        dump_map = {-1003945188229: frozenset({193})}
        live = SimpleNamespace(
            chat=SimpleNamespace(id=-1003903313717), message_thread_id=6
        )
        other_topic = SimpleNamespace(
            chat=SimpleNamespace(id=-1003903313717), message_thread_id=99
        )
        dump = SimpleNamespace(
            chat=SimpleNamespace(id=-1003945188229), message_thread_id=193
        )
        admin_other = SimpleNamespace(
            chat=SimpleNamespace(id=-1003945188229), message_thread_id=1
        )
        self.assertTrue(
            is_silent_read_chat(live, live_map=live_map, participant_map=dump_map)
        )
        self.assertTrue(
            is_silent_read_chat(
                other_topic, live_map=live_map, participant_map=dump_map
            )
        )
        self.assertTrue(
            is_silent_read_chat(dump, live_map=live_map, participant_map=dump_map)
        )
        self.assertFalse(
            is_silent_read_chat(
                admin_other, live_map=live_map, participant_map=dump_map
            )
        )


class _Stor:
    async def get_user(self, user_id: int):
        return None

    async def is_bot_admin(self, user_id: int) -> bool:
        return False

    async def user_has_active_license(self, user_id: int) -> bool:
        return False


class GroupAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_message_allowed_when_admin_only(self) -> None:
        policy = LicenseWhitelistPolicy(
            _Stor(), super_admin_id=1, admin_only_mode=True
        )
        msg = SimpleNamespace(
            chat=SimpleNamespace(type="supergroup"),
            text="привет",
        )
        self.assertTrue(is_groupish_event(msg))
        ctx = AccessContext(user_id=99, event_type="message", raw_event_type="message")
        decision = await policy.decide(None, ctx, msg)
        self.assertEqual(decision, AccessDecision.ALLOW)

    async def test_private_non_admin_denied(self) -> None:
        policy = LicenseWhitelistPolicy(
            _Stor(), super_admin_id=1, admin_only_mode=True
        )
        msg = SimpleNamespace(chat=SimpleNamespace(type="private"), text="привет")
        ctx = AccessContext(user_id=99, event_type="message", raw_event_type="message")
        decision = await policy.decide(None, ctx, msg)
        self.assertEqual(decision, AccessDecision.DENY_ADMIN_ONLY)


if __name__ == "__main__":
    unittest.main()

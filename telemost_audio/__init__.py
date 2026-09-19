"""Мини-подкасты (~1 мин) из аудио-записей Телемоста → голосовые в Telegram."""

from telemost_audio.pipeline import enqueue_telemost_audio, enqueue_telemost_audio_last

__all__ = ["enqueue_telemost_audio", "enqueue_telemost_audio_last"]

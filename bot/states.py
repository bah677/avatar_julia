from aiogram.fsm.state import State, StatesGroup


class SupportStates(StatesGroup):
    """Состояния системы поддержки."""
    waiting_for_message = State()


class StyleUploadStates(StatesGroup):
    waiting_file = State()


class DislikeReasonStates(StatesGroup):
    waiting_text = State()


class IntakeFixStates(StatesGroup):
    waiting_index = State()


class MenuFocusStates(StatesGroup):
    waiting_text = State()


class PassportTalkStates(StatesGroup):
    talking = State()


class StoriesUploadStates(StatesGroup):
    collecting = State()

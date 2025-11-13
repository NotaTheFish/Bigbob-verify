from __future__ import annotations

from telegram import KeyboardButton, ReplyKeyboardMarkup

MENU_VERIFICATION = "Верификация"
MENU_SHOP = "Магазин"
MENU_PROFILE = "Профиль"
MENU_SUPPORT = "Поддержка"
MENU_ADMIN = "Админ режим"


def build_main_keyboard(verified: bool, is_admin: bool) -> ReplyKeyboardMarkup:
    if not verified:
        return ReplyKeyboardMarkup(
            [[KeyboardButton(MENU_VERIFICATION)]],
            resize_keyboard=True,
            one_time_keyboard=False,
        )

    buttons = [
        [KeyboardButton(MENU_SHOP), KeyboardButton(MENU_PROFILE)],
        [KeyboardButton(MENU_SUPPORT)],
    ]
    if is_admin:
        buttons.append([KeyboardButton(MENU_ADMIN)])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=False)


__all__ = [
    "build_main_keyboard",
    "MENU_ADMIN",
    "MENU_PROFILE",
    "MENU_SHOP",
    "MENU_SUPPORT",
    "MENU_VERIFICATION",
]
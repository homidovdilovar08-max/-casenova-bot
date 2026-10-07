"""Telegram bot для кейсов и виртуального баланса CaseNova."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

if __package__:
    from .case_config import CASES, CASE_BY_ID, describe_case
    from .payment_config import (
        PROFILE_BADGE_DESCRIPTION,
        PROFILE_BADGE_PRODUCT_ID,
        PROFILE_BADGE_TITLE,
        PaymentConfig,
    )
    from .referral_config import REFERRAL_BONUS
    from .store import (
        AdminPaymentRow,
        CaseNovaStore,
        CaseOpening,
        InsufficientBalance,
        OpeningHistoryEntry,
        PaymentResult,
        RefundTarget,
        ReferralStats,
        TermsNotAccepted,
    )
else:
    from case_config import CASES, CASE_BY_ID, describe_case
    from payment_config import (
        PROFILE_BADGE_DESCRIPTION,
        PROFILE_BADGE_PRODUCT_ID,
        PROFILE_BADGE_TITLE,
        PaymentConfig,
    )
    from referral_config import REFERRAL_BONUS
    from store import (
        AdminPaymentRow,
        CaseNovaStore,
        CaseOpening,
        InsufficientBalance,
        OpeningHistoryEntry,
        PaymentResult,
        RefundTarget,
        ReferralStats,
        TermsNotAccepted,
    )

logger = logging.getLogger("telegram_bot")

CASES_BUTTON = "🎁 Кейсы"
PROFILE_BUTTON = "👤 Профиль"
REFERRALS_BUTTON = "👥 Рефералы"
BALANCE_BUTTON = "💰 Баланс"
HISTORY_BUTTON = "📜 История"
OPEN_CASE_BUTTON = "🎁 Открыть кейс"
SHOP_BUTTON = "🛍 Покупки"

MENU_BUTTONS = (CASES_BUTTON, PROFILE_BUTTON, REFERRALS_BUTTON, BALANCE_BUTTON, SHOP_BUTTON)
MENU_KEYBOARD = {
    "keyboard": [
        [CASES_BUTTON, PROFILE_BUTTON],
        [REFERRALS_BUTTON, BALANCE_BUTTON],
        [SHOP_BUTTON],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Выберите раздел",
}
PROFILE_KEYBOARD = {
    "keyboard": [
        [CASES_BUTTON, PROFILE_BUTTON],
        [REFERRALS_BUTTON, BALANCE_BUTTON],
        [SHOP_BUTTON],
        [HISTORY_BUTTON],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Выберите раздел",
}


def build_reply(
    text: str,
    user: dict[str, Any] | None = None,
    balance: int = 5000,
    entitlements: list[str] | None = None,
) -> str:
    """Сформировать русскоязычный ответ на команду или выбор раздела."""
    words = text.strip().split(maxsplit=1)
    command = words[0].split("@", maxsplit=1)[0].lower() if words else ""

    if command == "/start":
        return (
            "Добро пожаловать в CaseNova!\n"
            "Каждому новому пользователю начисляется 5000 виртуальных ⭐.\n"
            "Выберите раздел в меню. Виртуальные ⭐ — это игровые баллы, "
            "не деньги и не Telegram Stars."
        )
    if command == "/help":
        return "Выберите нужный раздел в меню CaseNova."
    if command.startswith("/"):
        return "Неизвестная команда. Выберите нужный раздел в меню."

    if text == CASES_BUTTON:
        return (
            "🎁 Кейсы\n"
            "Выберите кейс ниже. Все цены и награды указаны в виртуальных ⭐. "
            "Это игровые баллы, не деньги и не Telegram Stars."
        )
    if text == PROFILE_BUTTON:
        user = user or {}
        name = " ".join(
            part
            for part in (user.get("first_name"), user.get("last_name"))
            if isinstance(part, str) and part
        ) or "не указано"
        username = user.get("username")
        username_text = f"@{username}" if isinstance(username, str) else "не указано"
        user_id = user.get("id", "—")
        reply = (
            "👤 Профиль\n"
            f"Имя: {name}\n"
            f"Имя пользователя: {username_text}\n"
            f"Ваш ID: {user_id}\n"
            f"Баланс: {balance} виртуальных ⭐"
        )
        if entitlements and PROFILE_BADGE_PRODUCT_ID in entitlements:
            reply += "\n🎖 Цифровой продукт: значок CaseNova (оплачен в Telegram Stars)"
        return reply
    if text == REFERRALS_BUTTON:
        return (
            "👥 Рефералы\n"
            "Реферальная программа пока не запущена. "
            "Ссылка для приглашения появится здесь, когда будет доступна."
        )
    if text == BALANCE_BUTTON:
        return f"💰 Ваш баланс: {balance} виртуальных ⭐."
    if text == HISTORY_BUTTON:
        return "📜 История открытий появится здесь."
    return "Используйте кнопки меню или команды /start и /help."


def command_name(text: str) -> str:
    words = text.strip().split(maxsplit=1)
    return words[0].split("@", maxsplit=1)[0].lower() if words else ""


def build_terms_reply(terms_version: str) -> str:
    return (
        f"📄 Условия покупки CaseNova · версия {terms_version}\n\n"
        f"1. В магазине доступен только цифровой товар «{PROFILE_BADGE_TITLE}».\n"
        "2. Стоимость указывается в Telegram Stars (XTR) и списывается Telegram.\n"
        "3. Покупка выдаёт значок в профиле после подтверждения успешного платежа "
        "Telegram. Она не зачисляет виртуальные игровые ⭐ и не меняет баланс.\n"
        "4. Значок не открывает кейсы, не влияет на вероятности и не даёт шанса "
        "получить случайный или физический приз.\n"
        "5. Для вопросов по оплате используйте /paysupport. Возврат может "
        "выполнить администратор после проверки платежа.\n\n"
        "Telegram Stars и виртуальные игровые ⭐ — разные вещи."
    )


def build_payment_support_reply(support_username: str | None) -> str:
    contact = (
        f"Напишите @{support_username}"
        if support_username
        else "Обратитесь к администратору CaseNova"
    )
    return (
        "🆘 Поддержка по платежам\n"
        f"{contact} и укажите ID заказа, примерное время и сумму в XTR.\n"
        "Не отправляйте токены, пароли или коды подтверждения.\n"
        "Виртуальные игровые ⭐ не являются Telegram Stars."
    )


def build_admin_payments_reply(rows: list[AdminPaymentRow]) -> str:
    if not rows:
        return "🛡 Администрирование платежей\nЗаказов пока нет."

    status_labels = {
        "pending": "ожидает оплаты",
        "pre_checkout": "проверка перед оплатой",
        "paid": "оплачен",
        "refund_pending": "возврат обрабатывается",
        "refunded": "возвращён",
        "review": "требует проверки",
        "failed": "ошибка",
    }
    lines = ["🛡 Последние заказы CaseNova:"]
    for row in rows:
        order_status = status_labels.get(row.order_status, row.order_status)
        payment_status = (
            status_labels.get(row.payment_status, row.payment_status)
            if row.payment_status
            else "платёж не получен"
        )
        lines.extend(
            [
                f"• Заказ {row.order_id}",
                f"  Пользователь: {row.telegram_user_id}",
                f"  Товар: {row.product_id}; сумма: {row.amount_xtr} XTR",
                f"  Заказ: {order_status}; платёж: {payment_status}",
                f"  Telegram charge ID: {row.charge_id or '—'}",
                f"  Время: {row.created_at}",
            ]
        )
    lines.append("Возврат: /refund <ID заказа или Telegram charge ID>")
    return "\n".join(lines)


def build_history_reply(entries: list[OpeningHistoryEntry]) -> str:
    if not entries:
        return "📜 История\nВы ещё не открывали кейсы."

    lines = ["📜 Последние открытия кейсов:"]
    for entry in entries:
        lines.append(
            f"• {entry.case_title}: цена {entry.case_price} ⭐, "
            f"награда {entry.reward_amount} ⭐ — {entry.opened_at}"
        )
    return "\n".join(lines)


def build_opening_reply(opening: CaseOpening, current_balance: int) -> str:
    return (
        f"🎉 Кейс «{opening.case_title}» открыт!\n"
        f"Списано: {opening.case_price} виртуальных ⭐\n"
        f"Вы получили: {opening.reward_amount} виртуальных ⭐\n"
        f"Текущий баланс: {current_balance} виртуальных ⭐"
    )


def is_start_command(text: str) -> bool:
    words = text.strip().split(maxsplit=1)
    command = words[0].split("@", maxsplit=1)[0].lower() if words else ""
    return command == "/start"


def parse_referrer_id(text: str) -> int | None:
    words = text.strip().split(maxsplit=1)
    command = words[0].split("@", maxsplit=1)[0].lower() if words else ""
    if command != "/start" or len(words) < 2:
        return None

    payload = words[1]
    if not payload.startswith("ref_"):
        return None
    user_id = payload.removeprefix("ref_")
    if not user_id or len(user_id) > 20 or not user_id.isascii() or not user_id.isdigit():
        return None
    parsed_user_id = int(user_id)
    if parsed_user_id > 9223372036854775807:
        return None
    return parsed_user_id


def build_referral_reply(
    user_id: int,
    bot_username: str,
    stats: ReferralStats,
    referral_bonus: int = REFERRAL_BONUS,
) -> str:
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    return (
        "👥 Рефералы\n"
        f"Ваша личная ссылка:\n{link}\n\n"
        f"Успешных приглашений: {stats.successful_referrals}\n"
        f"Заработано на приглашениях: {stats.earned_bonus} виртуальных ⭐\n\n"
        "За каждого нового пользователя, который впервые запустит бота "
        f"по вашей ссылке, вам начисляется {referral_bonus} виртуальных ⭐."
    )


class TelegramBot:
    def __init__(
        self,
        token: str,
        store: CaseNovaStore | None = None,
        payment_config: PaymentConfig | None = None,
    ) -> None:
        self._api_url = f"https://api.telegram.org/bot{token}"
        self._store = store or CaseNovaStore()
        self._payment_config = payment_config or PaymentConfig()
        self._bot_username: str | None = None

    def _get_bot_username(self) -> str:
        if self._bot_username is not None:
            return self._bot_username

        identity = self._request("getMe", {})
        username = identity.get("username") if isinstance(identity, dict) else None
        if not isinstance(username, str) or not username:
            raise RuntimeError("Не удалось получить имя пользователя Telegram-бота.")
        self._bot_username = username
        return username

    def _request(
        self,
        method: str,
        payload: dict[str, Any],
        timeout: int = 40,
    ) -> Any:
        request = urllib.request.Request(
            f"{self._api_url}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise RuntimeError(
                "Не удалось связаться с Telegram. Проверьте подключение к интернету и настройки бота."
            ) from None

        if not result.get("ok"):
            raise RuntimeError("Telegram вернул ошибку. Проверьте настройки бота и токен.")
        return result["result"]

    def _send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = MENU_KEYBOARD,
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._request("sendMessage", payload)

    def _send_terms(self, chat_id: int | str) -> None:
        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ Принимаю условия покупки",
                        "callback_data": f"accept_terms:{self._payment_config.terms_version}",
                    }
                ]
            ]
        }
        self._send_message(
            chat_id,
            build_terms_reply(self._payment_config.terms_version),
            reply_markup=reply_markup,
        )

    def _send_shop(self, chat_id: int | str, user_id: int) -> None:
        if not self._payment_config.payments_enabled:
            self._send_message(
                chat_id,
                "🛍 Покупки через Telegram Stars сейчас выключены в безопасном режиме.\n"
                "Ваш виртуальный игровой баланс и обычные кейсы работают отдельно. "
                "Покупка не будет создана, пока настройки не проверены.",
            )
            return
        if not self._store.has_accepted_terms(
            user_id,
            self._payment_config.terms_version,
        ):
            self._send_terms(chat_id)
            return

        price = self._payment_config.profile_badge_price_xtr
        if price is None:
            return
        self._send_message(
            chat_id,
            "🛍 Цифровые товары\n"
            f"{PROFILE_BADGE_TITLE}\n{PROFILE_BADGE_DESCRIPTION}\n"
            f"Цена: {price} Telegram Stars (XTR).\n"
            "Оплата не пополняет виртуальный баланс и не связана с кейсами.",
            reply_markup={
                "inline_keyboard": [
                    [
                        {
                            "text": f"Купить за {price} XTR",
                            "callback_data": f"buy_product:{PROFILE_BADGE_PRODUCT_ID}",
                        }
                    ]
                ]
            },
        )

    def _create_and_send_invoice(self, chat_id: int | str, user_id: int) -> None:
        price = self._payment_config.profile_badge_price_xtr
        if price is None or not self._payment_config.payments_enabled:
            self._send_message(
                chat_id,
                "Покупки временно недоступны. Telegram Stars не списаны.",
            )
            return
        if not self._store.has_accepted_terms(user_id, self._payment_config.terms_version):
            self._send_terms(chat_id)
            return

        order_id = f"cn_{uuid.uuid4().hex}"
        try:
            self._store.create_payment_order(
                order_id=order_id,
                user_id=user_id,
                product_id=PROFILE_BADGE_PRODUCT_ID,
                amount_xtr=price,
                terms_version=self._payment_config.terms_version,
            )
        except TermsNotAccepted:
            self._send_terms(chat_id)
            return

        try:
            self._request(
                "sendInvoice",
                {
                    "chat_id": chat_id,
                    "title": PROFILE_BADGE_TITLE,
                    "description": (
                        f"{PROFILE_BADGE_DESCRIPTION}\nID заказа: {order_id}"
                    ),
                    "payload": order_id,
                    "provider_token": "",
                    "currency": "XTR",
                    "prices": [{"label": PROFILE_BADGE_TITLE, "amount": price}],
                },
            )
        except RuntimeError:
            logger.warning("Не удалось отправить счёт Telegram Stars для заказа %s.", order_id)
            self._send_message(
                chat_id,
                "Не удалось отправить счёт. Telegram Stars не списаны. "
                "Попробуйте позже или используйте /paysupport.",
            )

    def _answer_pre_checkout(
        self,
        query_id: str,
        ok: bool,
        error_message: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "pre_checkout_query_id": query_id,
            "ok": ok,
        }
        if error_message is not None:
            payload["error_message"] = error_message
        self._request("answerPreCheckoutQuery", payload, timeout=7)

    def _handle_pre_checkout_query(self, query: dict[str, Any]) -> None:
        query_id = query.get("id")
        user_id = (query.get("from") or {}).get("id")
        order_id = query.get("invoice_payload")
        amount = query.get("total_amount")
        currency = query.get("currency")
        if (
            not isinstance(query_id, str)
            or not isinstance(user_id, int)
            or not isinstance(order_id, str)
            or not isinstance(amount, int)
            or not isinstance(currency, str)
        ):
            logger.warning("Получен некорректный pre_checkout_query.")
            if isinstance(query_id, str):
                self._answer_pre_checkout(
                    query_id,
                    False,
                    "Не удалось проверить заказ. Создайте новый счёт через раздел покупок.",
                )
            return

        if not self._payment_config.payments_enabled:
            self._answer_pre_checkout(
                query_id,
                False,
                "Покупки временно выключены. Telegram Stars не будут списаны.",
            )
            return

        try:
            valid, reason = self._store.validate_pre_checkout(
                order_id=order_id,
                user_id=user_id,
                amount_xtr=amount,
                currency=currency,
                terms_version=self._payment_config.terms_version,
            )
        except sqlite3.Error:
            logger.exception("Не удалось проверить заказ перед оплатой.")
            valid, reason = False, "Не удалось проверить заказ. Попробуйте позже."
        self._answer_pre_checkout(
            query_id,
            valid,
            None if valid else reason,
        )

    def _handle_successful_payment(self, message: dict[str, Any]) -> None:
        payment = message.get("successful_payment") or {}
        user_id = (message.get("from") or {}).get("id")
        chat_id = (message.get("chat") or {}).get("id")
        order_id = payment.get("invoice_payload")
        charge_id = payment.get("telegram_payment_charge_id")
        amount = payment.get("total_amount")
        currency = payment.get("currency")
        if (
            not isinstance(user_id, int)
            or not isinstance(chat_id, (int, str))
            or not isinstance(charge_id, str)
            or not charge_id
            or not isinstance(amount, int)
            or not isinstance(currency, str)
        ):
            logger.error("Не удалось разобрать successful_payment; товар не выдан.")
            if isinstance(chat_id, (int, str)):
                self._send_message(
                    chat_id,
                    "Не удалось проверить платёж. Цифровой товар пока не выдан. "
                    "Пожалуйста, обратитесь через /paysupport.",
                )
            return
        if not isinstance(order_id, str):
            order_id = f"unknown_{uuid.uuid4().hex}"

        try:
            result = self._store.record_successful_payment(
                order_id=order_id,
                user_id=user_id,
                charge_id=charge_id,
                amount=amount,
                currency=currency,
            )
        except sqlite3.Error:
            logger.exception("Не удалось сохранить успешный платёж Telegram Stars.")
            self._send_message(
                chat_id,
                "Платёж получен, но заказ пока не подтверждён в CaseNova. "
                "Товар не выдан. Обратитесь через /paysupport.",
            )
            return

        if result.duplicate:
            self._send_message(
                chat_id,
                "Этот платёж уже обработан. Повторная выдача цифрового товара не выполнялась.",
            )
        elif result.delivered:
            self._send_message(
                chat_id,
                f"✅ Оплата подтверждена Telegram: {amount} XTR.\n"
                f"ID заказа: {order_id}\n"
                "Цифровой значок CaseNova добавлен в профиль.\n"
                "Виртуальный игровой баланс не изменён.",
            )
        else:
            logger.error(
                "Платёж %s для заказа %s сохранён со статусом review.",
                charge_id,
                order_id,
            )
            self._send_message(
                chat_id,
                "Платёж получен, но заказ требует проверки. "
                "Цифровой товар пока не выдан. Обратитесь через /paysupport.",
            )

    def _handle_admin_command(
        self,
        chat_id: int | str,
        user_id: int,
        chat_type: str | None,
    ) -> None:
        if not self._payment_config.is_admin(user_id):
            self._send_message(chat_id, "⛔ Доступ к разделу администратора запрещён.")
            return
        if chat_type != "private":
            self._send_message(chat_id, "Откройте личный чат с ботом для просмотра платежей.")
            return
        self._send_message(
            chat_id,
            build_admin_payments_reply(self._store.list_admin_payments()),
        )

    def _handle_refund_command(
        self,
        chat_id: int | str,
        user_id: int,
        chat_type: str | None,
        text: str,
    ) -> None:
        if not self._payment_config.is_admin(user_id):
            self._send_message(chat_id, "⛔ Выполнять возвраты может только администратор.")
            return
        if chat_type != "private":
            self._send_message(chat_id, "Выполняйте возврат только в личном чате с ботом.")
            return
        words = text.strip().split(maxsplit=1)
        if len(words) < 2 or not words[1].strip():
            self._send_message(
                chat_id,
                "Укажите ID заказа или Telegram charge ID: /refund <ID>",
            )
            return

        target = self._store.prepare_refund(words[1].strip())
        if target is None:
            self._send_message(
                chat_id,
                "Подходящий оплаченный платёж не найден или по нему уже выполняется "
                "возврат.",
            )
            return
        try:
            refunded = self._request(
                "refundStarPayment",
                {
                    "user_id": target.telegram_user_id,
                    "telegram_payment_charge_id": target.charge_id,
                },
            )
        except RuntimeError:
            logger.warning(
                "Ответ Telegram по возврату %s неизвестен; заказ оставлен на сверке.",
                target.charge_id,
            )
            self._send_message(
                chat_id,
                "Не удалось однозначно проверить ответ Telegram. Возврат отмечен "
                "как требующий сверки; не повторяйте его до проверки статуса заказа.",
            )
            return

        if refunded is not True:
            self._store.cancel_refund(target.charge_id)
            self._send_message(
                chat_id,
                "Telegram не подтвердил возврат. Статус платежа восстановлен.",
            )
            return

        if not self._store.complete_refund(target.charge_id):
            logger.critical(
                "Telegram вернул платёж %s, но локальный статус не обновлён.",
                target.charge_id,
            )
            self._send_message(
                chat_id,
                "Telegram выполнил возврат, но локальный статус не обновился. "
                "Заказ требует ручной сверки.",
            )
            return
        self._send_message(
            chat_id,
            f"✅ Возврат подтверждён Telegram для заказа {target.order_id}.",
        )

    def _send_case_catalog(self, chat_id: int | str) -> None:
        self._send_message(chat_id, build_reply(CASES_BUTTON))
        for case in CASES:
            inline_keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": OPEN_CASE_BUTTON,
                            "callback_data": f"open_case:{case.case_id}",
                        }
                    ]
                ]
            }
            self._send_message(
                chat_id,
                describe_case(case),
                reply_markup=inline_keyboard,
            )

    def _handle_message(self, message: dict[str, Any]) -> None:
        successful_payment = message.get("successful_payment")
        if isinstance(successful_payment, dict):
            self._handle_successful_payment(message)
            return

        text = message.get("text")
        user = message.get("from") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        user_id = user.get("id")
        if not isinstance(text, str) or not isinstance(user_id, int) or chat_id is None:
            return

        registration = self._store.register_user(
            user_id,
            referrer_user_id=parse_referrer_id(text),
        )
        balance = registration.balance
        if is_start_command(text):
            reply = build_reply(text, user, balance)
            if registration.referral_registered:
                reply += (
                    "\nПользователь, который вас пригласил, получил бонус: "
                    f"{registration.referral_bonus} виртуальных ⭐."
                )
            self._send_message(chat_id, reply)
            return
        if text == CASES_BUTTON:
            self._send_case_catalog(chat_id)
            return
        command = command_name(text)
        if command == "/terms":
            self._send_terms(chat_id)
            return
        if command == "/paysupport":
            self._send_message(
                chat_id,
                build_payment_support_reply(self._payment_config.support_username),
            )
            return
        if command == "/shop" or text == SHOP_BUTTON:
            if chat_type != "private":
                self._send_message(chat_id, "Покупки доступны только в личном чате с ботом.")
                return
            self._send_shop(chat_id, user_id)
            return
        if command == "/admin":
            self._handle_admin_command(chat_id, user_id, chat_type)
            return
        if command == "/refund":
            self._handle_refund_command(chat_id, user_id, chat_type, text)
            return
        if text == PROFILE_BUTTON:
            self._send_message(
                chat_id,
                build_reply(
                    text,
                    user,
                    balance,
                    entitlements=self._store.get_active_entitlements(user_id),
                ),
                reply_markup=PROFILE_KEYBOARD,
            )
            return
        if text == REFERRALS_BUTTON:
            reply = build_referral_reply(
                user_id=user_id,
                bot_username=self._get_bot_username(),
                stats=self._store.get_referral_stats(user_id),
                referral_bonus=self._store.referral_bonus,
            )
            self._send_message(chat_id, reply)
            return
        if text == HISTORY_BUTTON:
            self._send_message(
                chat_id,
                build_history_reply(self._store.get_history(user_id)),
            )
            return

        self._send_message(chat_id, build_reply(text, user, balance))

    def _answer_callback(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text is not None:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True
        self._request("answerCallbackQuery", payload)

    def _handle_callback_query(self, query: dict[str, Any]) -> None:
        callback_query_id = query.get("id")
        data = query.get("data")
        user = query.get("from") or {}
        message = query.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        user_id = user.get("id")
        if (
            not isinstance(callback_query_id, str)
            or not isinstance(data, str)
            or not isinstance(user_id, int)
            or chat_id is None
        ):
            return

        prefix = "open_case:"
        if not data.startswith(prefix):
            accept_prefix = "accept_terms:"
            if data.startswith(accept_prefix):
                version = data.removeprefix(accept_prefix)
                if version != self._payment_config.terms_version:
                    self._answer_callback(
                        callback_query_id,
                        "Условия обновились. Ознакомьтесь с актуальной версией.",
                        show_alert=True,
                    )
                    self._send_terms(chat_id)
                    return
                self._store.accept_terms(user_id, version)
                self._answer_callback(callback_query_id, "Условия приняты.")
                self._send_shop(chat_id, user_id)
                return

            buy_prefix = "buy_product:"
            if data.startswith(buy_prefix):
                product_id = data.removeprefix(buy_prefix)
                if product_id != PROFILE_BADGE_PRODUCT_ID:
                    self._answer_callback(
                        callback_query_id,
                        "Такого цифрового товара нет.",
                        show_alert=True,
                    )
                    return
                if not self._payment_config.payments_enabled:
                    self._answer_callback(
                        callback_query_id,
                        "Покупки выключены в безопасном режиме.",
                        show_alert=True,
                    )
                    return
                if not self._store.has_accepted_terms(
                    user_id,
                    self._payment_config.terms_version,
                ):
                    self._answer_callback(
                        callback_query_id,
                        "Сначала ознакомьтесь с условиями покупки.",
                        show_alert=True,
                    )
                    self._send_terms(chat_id)
                    return
                self._answer_callback(callback_query_id, "Готовим счёт Telegram Stars.")
                self._create_and_send_invoice(chat_id, user_id)
                return

            self._answer_callback(callback_query_id, "Неизвестное действие.", show_alert=True)
            return

        case_id = data.removeprefix(prefix)
        try:
            opening, is_new = self._store.open_case(
                user_id=user_id,
                case_id=case_id,
                callback_query_id=callback_query_id,
            )
        except InsufficientBalance as error:
            self._answer_callback(callback_query_id, str(error), show_alert=True)
            return
        except ValueError:
            self._answer_callback(callback_query_id, "Такого кейса нет.", show_alert=True)
            return

        if not is_new:
            self._answer_callback(callback_query_id, "Это открытие уже обработано.")
            return

        self._answer_callback(callback_query_id, "Кейс открыт!")
        current_balance = self._store.get_balance(user_id)
        self._send_message(
            chat_id,
            build_opening_reply(opening, current_balance),
        )

    def _process_update(self, update: dict[str, Any]) -> None:
        pre_checkout_query = update.get("pre_checkout_query")
        if isinstance(pre_checkout_query, dict):
            self._handle_pre_checkout_query(pre_checkout_query)
            return

        message = update.get("message")
        if isinstance(message, dict):
            self._handle_message(message)
            return

        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            self._handle_callback_query(callback_query)

    def run(self) -> None:
        offset: int | None = None
        logger.info("Бот запущен. Для остановки нажмите Ctrl+C.")

        while True:
            payload: dict[str, Any] = {
                "timeout": 30,
                "allowed_updates": ["message", "callback_query", "pre_checkout_query"],
            }
            if offset is not None:
                payload["offset"] = offset

            try:
                updates = self._request("getUpdates", payload)
                for update in updates:
                    self._process_update(update)
                    offset = update["update_id"] + 1
            except RuntimeError as error:
                logger.error("%s Повторная попытка через 5 секунд.", error)
                time.sleep(5)
            except sqlite3.Error:
                logger.error("Ошибка локального хранилища. Повторная попытка через 5 секунд.")
                time.sleep(5)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "Не задана переменная TELEGRAM_BOT_TOKEN. Добавьте токен бота в секреты проекта."
        )

    try:
        payment_config = PaymentConfig.from_env()
        if payment_config.requested_enabled and not payment_config.payments_enabled:
            logger.error(
                "Покупки отключены: настройки не прошли проверку (%s).",
                "; ".join(payment_config.configuration_issues),
            )
        TelegramBot(token, payment_config=payment_config).run()
    except KeyboardInterrupt:
        logger.info("Бот остановлен.")
    except sqlite3.Error:
        raise SystemExit(
            "Не удалось открыть хранилище CaseNova. Проверьте доступ к файлам проекта."
        ) from None


if __name__ == "__main__":
    main()

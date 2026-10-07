"""Fail-closed settings and the fixed digital product offered for Telegram Stars."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

PROFILE_BADGE_PRODUCT_ID = "profile_badge"
PROFILE_BADGE_TITLE = "Значок CaseNova"
PROFILE_BADGE_DESCRIPTION = (
    "Постоянный цифровой значок в профиле. "
    "Не меняет виртуальный баланс, кейсы или награды."
)
DEFAULT_TERMS_VERSION = "v1"


@dataclass(frozen=True)
class PaymentConfig:
    requested_enabled: bool = False
    admin_ids: frozenset[int] = frozenset()
    profile_badge_price_xtr: int | None = None
    terms_version: str = DEFAULT_TERMS_VERSION
    support_username: str | None = None

    @property
    def configuration_issues(self) -> tuple[str, ...]:
        issues: list[str] = []
        if not self.admin_ids:
            issues.append("не настроен список администраторов")
        if self.profile_badge_price_xtr is None or self.profile_badge_price_xtr <= 0:
            issues.append("не задана положительная цена значка в XTR")
        if not self.support_username:
            issues.append("не задано имя пользователя службы поддержки")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", self.terms_version):
            issues.append("неверно задана версия условий покупки")
        return tuple(issues)

    @property
    def payments_enabled(self) -> bool:
        return self.requested_enabled and not self.configuration_issues

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> PaymentConfig:
        values = os.environ if environ is None else environ
        enabled = values.get("TELEGRAM_PAYMENTS_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }

        raw_admin_ids = values.get("TELEGRAM_ADMIN_IDS", "").strip()
        admin_ids: set[int] = set()
        if raw_admin_ids:
            pieces = [piece.strip() for piece in raw_admin_ids.split(",")]
            if all(
                piece.isascii()
                and piece.isdigit()
                and len(piece) <= 19
                and 0 < int(piece) <= 9223372036854775807
                for piece in pieces
            ):
                admin_ids = {int(piece) for piece in pieces}

        raw_price = values.get("TELEGRAM_PROFILE_BADGE_PRICE_XTR", "").strip()
        try:
            price = int(raw_price) if raw_price else None
        except ValueError:
            price = None
        if price is not None and price <= 0:
            price = None

        support_username = values.get("TELEGRAM_PAYMENT_SUPPORT_USERNAME", "").strip()
        support_username = support_username.removeprefix("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", support_username):
            support_username = ""

        terms_version = values.get(
            "TELEGRAM_TERMS_VERSION",
            DEFAULT_TERMS_VERSION,
        ).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", terms_version):
            terms_version = DEFAULT_TERMS_VERSION

        return cls(
            requested_enabled=enabled,
            admin_ids=frozenset(admin_ids),
            profile_badge_price_xtr=price,
            terms_version=terms_version,
            support_username=support_username or None,
        )

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

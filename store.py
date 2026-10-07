"""Локальное постоянное хранилище виртуальных балансов и открытий кейсов."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .case_config import CASE_BY_ID
    from .payment_config import PROFILE_BADGE_PRODUCT_ID
    from .referral_config import REFERRAL_BONUS
else:
    from case_config import CASE_BY_ID
    from payment_config import PROFILE_BADGE_PRODUCT_ID
    from referral_config import REFERRAL_BONUS

DEFAULT_STARTING_BALANCE = 5000


class InsufficientBalance(Exception):
    def __init__(self, required: int, available: int) -> None:
        self.required = required
        self.available = available
        super().__init__(
            f"Недостаточно виртуальных ⭐. Нужно: {required}, доступно: {available}."
        )


@dataclass(frozen=True)
class CaseOpening:
    case_id: str
    case_title: str
    case_price: int
    reward_amount: int
    balance_before: int
    balance_after: int
    opened_at: str


@dataclass(frozen=True)
class OpeningHistoryEntry:
    case_title: str
    case_price: int
    reward_amount: int
    opened_at: str


@dataclass(frozen=True)
class UserRegistration:
    balance: int
    is_new_user: bool
    referral_registered: bool
    referral_bonus: int


@dataclass(frozen=True)
class ReferralStats:
    successful_referrals: int
    earned_bonus: int


@dataclass(frozen=True)
class PaymentOrder:
    order_id: str
    telegram_user_id: int
    product_id: str
    amount_xtr: int
    status: str
    created_at: str
    paid_charge_id: str | None = None


@dataclass(frozen=True)
class PaymentResult:
    delivered: bool
    duplicate: bool
    status: str
    product_id: str


@dataclass(frozen=True)
class AdminPaymentRow:
    order_id: str
    telegram_user_id: int
    product_id: str
    amount_xtr: int
    order_status: str
    charge_id: str | None
    payment_status: str | None
    created_at: str


@dataclass(frozen=True)
class RefundTarget:
    order_id: str
    telegram_user_id: int
    charge_id: str
    product_id: str
    prior_status: str
    is_order_payment: bool


class TermsNotAccepted(Exception):
    """Raised when an order is created without accepting the current terms."""


class CaseNovaStore:
    def __init__(
        self,
        db_path: str | Path | None = None,
        starting_balance: int = DEFAULT_STARTING_BALANCE,
        referral_bonus: int = REFERRAL_BONUS,
    ) -> None:
        if starting_balance < 0:
            raise ValueError("Начальный баланс не может быть отрицательным.")
        if referral_bonus < 0:
            raise ValueError("Реферальный бонус не может быть отрицательным.")
        self.db_path = Path(db_path) if db_path else Path(__file__).with_name("casenova.sqlite3")
        self.starting_balance = starting_balance
        self.referral_bonus = referral_bonus
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    balance INTEGER NOT NULL CHECK (balance >= 0),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS case_openings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    callback_query_id TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL,
                    case_title TEXT NOT NULL,
                    case_price INTEGER NOT NULL CHECK (case_price >= 0),
                    reward_amount INTEGER NOT NULL CHECK (reward_amount >= 0),
                    balance_before INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS case_openings_user_id_id
                    ON case_openings(user_id, id DESC);

                CREATE TABLE IF NOT EXISTS referrals (
                    referred_user_id INTEGER PRIMARY KEY
                        REFERENCES users(user_id) ON DELETE CASCADE,
                    referrer_user_id INTEGER NOT NULL
                        REFERENCES users(user_id) ON DELETE CASCADE,
                    bonus_amount INTEGER NOT NULL CHECK (bonus_amount >= 0),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK (referred_user_id != referrer_user_id)
                );

                CREATE INDEX IF NOT EXISTS referrals_referrer_user_id
                    ON referrals(referrer_user_id);

                CREATE TABLE IF NOT EXISTS user_terms_acceptance (
                    user_id INTEGER PRIMARY KEY
                        REFERENCES users(user_id) ON DELETE CASCADE,
                    terms_version TEXT NOT NULL,
                    accepted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS payment_orders (
                    order_id TEXT PRIMARY KEY,
                    telegram_user_id INTEGER NOT NULL
                        REFERENCES users(user_id) ON DELETE CASCADE,
                    product_id TEXT NOT NULL,
                    amount_xtr INTEGER NOT NULL CHECK (amount_xtr > 0),
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'pending', 'pre_checkout', 'paid', 'refund_pending',
                            'refunded', 'review', 'failed'
                        )
                    ),
                    terms_version TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    paid_at TEXT,
                    paid_charge_id TEXT UNIQUE
                );

                CREATE INDEX IF NOT EXISTS payment_orders_created_at
                    ON payment_orders(created_at DESC);

                CREATE TABLE IF NOT EXISTS payment_transactions (
                    telegram_payment_charge_id TEXT PRIMARY KEY,
                    invoice_order_id TEXT NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    amount_xtr INTEGER NOT NULL CHECK (amount_xtr >= 0),
                    currency TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('paid', 'review', 'refund_pending', 'refunded')
                    ),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    refunded_at TEXT,
                    refund_previous_status TEXT
                );

                CREATE INDEX IF NOT EXISTS payment_transactions_order_id
                    ON payment_transactions(invoice_order_id);

                CREATE INDEX IF NOT EXISTS payment_transactions_created_at
                    ON payment_transactions(created_at DESC);

                CREATE TABLE IF NOT EXISTS user_entitlements (
                    order_id TEXT PRIMARY KEY
                        REFERENCES payment_orders(order_id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL
                        REFERENCES users(user_id) ON DELETE CASCADE,
                    product_id TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    granted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS user_entitlements_user_active
                    ON user_entitlements(user_id, active);
                """
            )
            connection.commit()
        finally:
            connection.close()

    def register_user(
        self,
        user_id: int,
        referrer_user_id: int | None = None,
    ) -> UserRegistration:
        """Создать пользователя и один раз начислить бонус за нового приглашённого."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            inserted_user = connection.execute(
                "INSERT OR IGNORE INTO users(user_id, balance) VALUES (?, ?)",
                (user_id, self.starting_balance),
            )
            is_new_user = inserted_user.rowcount == 1
            referral_registered = False
            referral_bonus = 0

            if (
                is_new_user
                and referrer_user_id is not None
                and referrer_user_id != user_id
            ):
                referrer_exists = connection.execute(
                    "SELECT 1 FROM users WHERE user_id = ?",
                    (referrer_user_id,),
                ).fetchone()
                if referrer_exists is not None:
                    connection.execute(
                        """
                        INSERT INTO referrals(
                            referred_user_id, referrer_user_id, bonus_amount
                        ) VALUES (?, ?, ?)
                        """,
                        (user_id, referrer_user_id, self.referral_bonus),
                    )
                    connection.execute(
                        "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                        (self.referral_bonus, referrer_user_id),
                    )
                    referral_registered = True
                    referral_bonus = self.referral_bonus

            row = connection.execute(
                "SELECT balance FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                raise sqlite3.DatabaseError("Не удалось создать пользователя.")
            connection.commit()
            return UserRegistration(
                balance=int(row[0]),
                is_new_user=is_new_user,
                referral_registered=referral_registered,
                referral_bonus=referral_bonus,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_referral_stats(self, user_id: int) -> ReferralStats:
        self.ensure_user(user_id)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(bonus_amount), 0)
                FROM referrals
                WHERE referrer_user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if row is None:
                raise sqlite3.DatabaseError("Не удалось получить статистику приглашений.")
            return ReferralStats(
                successful_referrals=int(row[0]),
                earned_bonus=int(row[1]),
            )
        finally:
            connection.close()

    def accept_terms(self, user_id: int, terms_version: str) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO users(user_id, balance) VALUES (?, ?)",
                    (user_id, self.starting_balance),
                )
                connection.execute(
                    """
                    INSERT INTO user_terms_acceptance(user_id, terms_version)
                    VALUES (?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        terms_version = excluded.terms_version,
                        accepted_at = CURRENT_TIMESTAMP
                    """,
                    (user_id, terms_version),
                )
        finally:
            connection.close()

    def has_accepted_terms(self, user_id: int, terms_version: str) -> bool:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT 1 FROM user_terms_acceptance
                WHERE user_id = ? AND terms_version = ?
                """,
                (user_id, terms_version),
            ).fetchone()
            return row is not None
        finally:
            connection.close()

    def create_payment_order(
        self,
        order_id: str,
        user_id: int,
        product_id: str,
        amount_xtr: int,
        terms_version: str,
    ) -> PaymentOrder:
        if product_id != PROFILE_BADGE_PRODUCT_ID:
            raise ValueError("Такого цифрового товара нет.")
        if amount_xtr <= 0:
            raise ValueError("Цена в Telegram Stars должна быть положительной.")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO users(user_id, balance) VALUES (?, ?)",
                (user_id, self.starting_balance),
            )
            accepted = connection.execute(
                """
                SELECT 1 FROM user_terms_acceptance
                WHERE user_id = ? AND terms_version = ?
                """,
                (user_id, terms_version),
            ).fetchone()
            if accepted is None:
                raise TermsNotAccepted("Перед покупкой примите актуальные условия.")

            connection.execute(
                """
                INSERT INTO payment_orders(
                    order_id, telegram_user_id, product_id, amount_xtr,
                    status, terms_version
                ) VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (order_id, user_id, product_id, amount_xtr, terms_version),
            )
            row = connection.execute(
                """
                SELECT order_id, telegram_user_id, product_id, amount_xtr,
                       status, created_at, paid_charge_id
                FROM payment_orders
                WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()
            if row is None:
                raise sqlite3.DatabaseError("Не удалось создать заказ.")
            connection.commit()
            return self._payment_order_from_row(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def validate_pre_checkout(
        self,
        order_id: str,
        user_id: int,
        amount_xtr: int,
        currency: str,
        terms_version: str,
    ) -> tuple[bool, str]:
        """Validate and persist pre-checkout state using a short SQLite wait."""
        connection = sqlite3.connect(self.db_path, timeout=1)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            order = connection.execute(
                """
                SELECT telegram_user_id, product_id, amount_xtr, status, terms_version
                FROM payment_orders
                WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()
            accepted = connection.execute(
                """
                SELECT 1 FROM user_terms_acceptance
                WHERE user_id = ? AND terms_version = ?
                """,
                (user_id, terms_version),
            ).fetchone()

            if order is None:
                connection.rollback()
                return False, "Заказ не найден. Создайте новый счёт через раздел покупок."
            if int(order[0]) != user_id:
                connection.rollback()
                return False, "Этот счёт создан для другого пользователя."
            if str(order[1]) != PROFILE_BADGE_PRODUCT_ID:
                connection.rollback()
                return False, "Этот цифровой товар сейчас недоступен."
            if int(order[2]) != amount_xtr or currency != "XTR":
                connection.rollback()
                return False, "Сумма или валюта счёта не совпадает с заказом."
            if str(order[4]) != terms_version or accepted is None:
                connection.rollback()
                return False, "Сначала примите актуальные условия командой /terms."
            if str(order[3]) not in {"pending", "pre_checkout"}:
                connection.rollback()
                return False, "Этот заказ уже обработан или больше не действует."

            connection.execute(
                """
                UPDATE payment_orders
                SET status = 'pre_checkout'
                WHERE order_id = ? AND status IN ('pending', 'pre_checkout')
                """,
                (order_id,),
            )
            connection.commit()
            return True, "Заказ проверен."
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_successful_payment(
        self,
        order_id: str,
        user_id: int,
        charge_id: str,
        amount: int,
        currency: str,
    ) -> PaymentResult:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_payment = connection.execute(
                """
                SELECT status, product_id FROM payment_transactions
                WHERE telegram_payment_charge_id = ?
                """,
                (charge_id,),
            ).fetchone()
            if existing_payment is not None:
                connection.commit()
                return PaymentResult(
                    delivered=False,
                    duplicate=True,
                    status=str(existing_payment[0]),
                    product_id=str(existing_payment[1]),
                )

            order = connection.execute(
                """
                SELECT telegram_user_id, product_id, amount_xtr, status, terms_version
                FROM payment_orders
                WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()

            product_id = str(order[1]) if order is not None else "unknown"
            valid_payment = (
                order is not None
                and int(order[0]) == user_id
                and str(order[1]) == PROFILE_BADGE_PRODUCT_ID
                and int(order[2]) == amount
                and currency == "XTR"
                and str(order[3]) in {"pending", "pre_checkout"}
                and self.has_accepted_terms(user_id, str(order[4]))
            )
            payment_status = "paid" if valid_payment else "review"
            connection.execute(
                """
                INSERT INTO payment_transactions(
                    telegram_payment_charge_id, invoice_order_id, telegram_user_id,
                    amount_xtr, currency, product_id, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    charge_id,
                    order_id,
                    user_id,
                    amount,
                    currency,
                    product_id,
                    payment_status,
                ),
            )

            if valid_payment:
                connection.execute(
                    """
                    UPDATE payment_orders
                    SET status = 'paid', paid_at = CURRENT_TIMESTAMP,
                        paid_charge_id = ?
                    WHERE order_id = ?
                    """,
                    (charge_id, order_id),
                )
                connection.execute(
                    """
                    INSERT INTO user_entitlements(order_id, user_id, product_id)
                    VALUES (?, ?, ?)
                    """,
                    (order_id, user_id, product_id),
                )
            elif order is not None and str(order[3]) in {"pending", "pre_checkout"}:
                connection.execute(
                    "UPDATE payment_orders SET status = 'review' WHERE order_id = ?",
                    (order_id,),
                )

            connection.commit()
            return PaymentResult(
                delivered=bool(valid_payment),
                duplicate=False,
                status=payment_status,
                product_id=product_id,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_active_entitlements(self, user_id: int) -> list[str]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT e.product_id
                FROM user_entitlements AS e
                JOIN payment_orders AS o ON o.order_id = e.order_id
                JOIN payment_transactions AS p
                    ON p.telegram_payment_charge_id = o.paid_charge_id
                WHERE e.user_id = ? AND e.active = 1
                  AND o.status = 'paid' AND p.status = 'paid'
                ORDER BY e.granted_at DESC
                """,
                (user_id,),
            ).fetchall()
            return [str(row[0]) for row in rows]
        finally:
            connection.close()

    def list_admin_payments(self, limit: int = 10) -> list[AdminPaymentRow]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT o.order_id, o.telegram_user_id, o.product_id,
                           o.amount_xtr, o.status, p.telegram_payment_charge_id,
                           p.status, COALESCE(p.created_at, o.created_at) AS created_at
                    FROM payment_orders AS o
                    LEFT JOIN payment_transactions AS p
                        ON p.invoice_order_id = o.order_id

                    UNION ALL

                    SELECT p.invoice_order_id, p.telegram_user_id, p.product_id,
                           p.amount_xtr, 'review', p.telegram_payment_charge_id,
                           p.status, p.created_at AS created_at
                    FROM payment_transactions AS p
                    WHERE NOT EXISTS (
                        SELECT 1 FROM payment_orders AS o
                        WHERE o.order_id = p.invoice_order_id
                    )
                )
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 50)),),
            ).fetchall()
            return [
                AdminPaymentRow(
                    order_id=str(row[0]),
                    telegram_user_id=int(row[1]),
                    product_id=str(row[2]),
                    amount_xtr=int(row[3]),
                    order_status=str(row[4]),
                    charge_id=str(row[5]) if row[5] is not None else None,
                    payment_status=str(row[6]) if row[6] is not None else None,
                    created_at=str(row[7]),
                )
                for row in rows
            ]
        finally:
            connection.close()

    def prepare_refund(self, reference: str) -> RefundTarget | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT p.invoice_order_id, p.telegram_user_id,
                       p.telegram_payment_charge_id, p.product_id, p.status,
                       o.paid_charge_id
                FROM payment_transactions AS p
                LEFT JOIN payment_orders AS o ON o.order_id = p.invoice_order_id
                WHERE p.telegram_payment_charge_id = ? OR p.invoice_order_id = ?
                ORDER BY CASE WHEN o.paid_charge_id = p.telegram_payment_charge_id
                              THEN 0 ELSE 1 END,
                         p.created_at DESC
                LIMIT 1
                """,
                (reference, reference),
            ).fetchone()
            if row is None or str(row[4]) not in {"paid", "review"}:
                connection.rollback()
                return None

            order_id = str(row[0])
            user_id = int(row[1])
            charge_id = str(row[2])
            product_id = str(row[3])
            prior_status = str(row[4])
            is_order_payment = row[5] == charge_id
            connection.execute(
                """
                UPDATE payment_transactions
                SET refund_previous_status = status, status = 'refund_pending'
                WHERE telegram_payment_charge_id = ?
                """,
                (charge_id,),
            )
            if is_order_payment:
                connection.execute(
                    """
                    UPDATE payment_orders
                    SET status = 'refund_pending'
                    WHERE order_id = ? AND status = 'paid'
                    """,
                    (order_id,),
                )
            connection.commit()
            return RefundTarget(
                order_id=order_id,
                telegram_user_id=user_id,
                charge_id=charge_id,
                product_id=product_id,
                prior_status=prior_status,
                is_order_payment=is_order_payment,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_refund(self, charge_id: str) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT invoice_order_id FROM payment_transactions
                WHERE telegram_payment_charge_id = ? AND status = 'refund_pending'
                """,
                (charge_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            order_id = str(row[0])
            connection.execute(
                """
                UPDATE payment_transactions
                SET status = 'refunded', refunded_at = CURRENT_TIMESTAMP,
                    refund_previous_status = NULL
                WHERE telegram_payment_charge_id = ?
                """,
                (charge_id,),
            )
            order = connection.execute(
                "SELECT paid_charge_id FROM payment_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            if order is not None and order[0] == charge_id:
                connection.execute(
                    """
                    UPDATE payment_orders
                    SET status = 'refunded'
                    WHERE order_id = ? AND status = 'refund_pending'
                    """,
                    (order_id,),
                )
                connection.execute(
                    "UPDATE user_entitlements SET active = 0 WHERE order_id = ?",
                    (order_id,),
                )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def cancel_refund(self, charge_id: str) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT invoice_order_id, refund_previous_status
                FROM payment_transactions
                WHERE telegram_payment_charge_id = ? AND status = 'refund_pending'
                """,
                (charge_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            order_id = str(row[0])
            prior_status = str(row[1] or "review")
            connection.execute(
                """
                UPDATE payment_transactions
                SET status = ?, refund_previous_status = NULL
                WHERE telegram_payment_charge_id = ?
                """,
                (prior_status, charge_id),
            )
            order = connection.execute(
                "SELECT paid_charge_id FROM payment_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            if order is not None and order[0] == charge_id:
                connection.execute(
                    """
                    UPDATE payment_orders SET status = 'paid'
                    WHERE order_id = ? AND status = 'refund_pending'
                    """,
                    (order_id,),
                )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ensure_user(self, user_id: int) -> int:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO users(user_id, balance) VALUES (?, ?)",
                    (user_id, self.starting_balance),
                )
                row = connection.execute(
                    "SELECT balance FROM users WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                if row is None:
                    raise sqlite3.DatabaseError("Не удалось создать пользователя.")
                return int(row[0])
        finally:
            connection.close()

    def get_balance(self, user_id: int) -> int:
        return self.ensure_user(user_id)

    def open_case(
        self,
        user_id: int,
        case_id: str,
        callback_query_id: str,
        rng: random.Random | None = None,
    ) -> tuple[CaseOpening, bool]:
        case = CASE_BY_ID.get(case_id)
        if case is None:
            raise ValueError("Такого кейса нет.")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")

            existing = connection.execute(
                """
                SELECT case_id, case_title, case_price, reward_amount,
                       balance_before, balance_after, opened_at
                FROM case_openings
                WHERE callback_query_id = ?
                """,
                (callback_query_id,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._opening_from_row(existing), False

            connection.execute(
                "INSERT OR IGNORE INTO users(user_id, balance) VALUES (?, ?)",
                (user_id, self.starting_balance),
            )
            user_row = connection.execute(
                "SELECT balance FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if user_row is None:
                raise sqlite3.DatabaseError("Не удалось найти баланс пользователя.")

            balance_before = int(user_row[0])
            if balance_before < case.price:
                raise InsufficientBalance(case.price, balance_before)

            chooser = rng.choices if rng is not None else random.choices
            reward = chooser(
                list(case.rewards),
                weights=[option.probability for option in case.rewards],
                k=1,
            )[0]
            balance_after = balance_before - case.price + reward.amount
            connection.execute(
                "UPDATE users SET balance = ? WHERE user_id = ?",
                (balance_after, user_id),
            )
            cursor = connection.execute(
                """
                INSERT INTO case_openings(
                    callback_query_id, user_id, case_id, case_title, case_price,
                    reward_amount, balance_before, balance_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    callback_query_id,
                    user_id,
                    case.case_id,
                    case.title,
                    case.price,
                    reward.amount,
                    balance_before,
                    balance_after,
                ),
            )
            row = connection.execute(
                """
                SELECT case_id, case_title, case_price, reward_amount,
                       balance_before, balance_after, opened_at
                FROM case_openings
                WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
            if row is None:
                raise sqlite3.DatabaseError("Не удалось сохранить открытие кейса.")
            connection.commit()
            return self._opening_from_row(row), True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_history(self, user_id: int, limit: int = 10) -> list[OpeningHistoryEntry]:
        self.ensure_user(user_id)
        safe_limit = max(1, min(limit, 50))
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT case_title, case_price, reward_amount, opened_at
                FROM case_openings
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, safe_limit),
            ).fetchall()
            return [
                OpeningHistoryEntry(
                    case_title=str(row[0]),
                    case_price=int(row[1]),
                    reward_amount=int(row[2]),
                    opened_at=str(row[3]),
                )
                for row in rows
            ]
        finally:
            connection.close()

    @staticmethod
    def _payment_order_from_row(row: tuple[object, ...]) -> PaymentOrder:
        return PaymentOrder(
            order_id=str(row[0]),
            telegram_user_id=int(row[1]),
            product_id=str(row[2]),
            amount_xtr=int(row[3]),
            status=str(row[4]),
            created_at=str(row[5]),
            paid_charge_id=str(row[6]) if row[6] is not None else None,
        )

    @staticmethod
    def _opening_from_row(row: tuple[object, ...]) -> CaseOpening:
        return CaseOpening(
            case_id=str(row[0]),
            case_title=str(row[1]),
            case_price=int(row[2]),
            reward_amount=int(row[3]),
            balance_before=int(row[4]),
            balance_after=int(row[5]),
            opened_at=str(row[6]),
        )

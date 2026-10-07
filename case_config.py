"""Настройки виртуальных кейсов CaseNova."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RewardOption:
    amount: int
    probability: int


@dataclass(frozen=True)
class CaseConfig:
    case_id: str
    title: str
    price: int
    rewards: tuple[RewardOption, ...]


CASES = (
    CaseConfig(
        case_id="novice",
        title="🟢 Новичок",
        price=100,
        rewards=(
            RewardOption(40, 50),
            RewardOption(75, 30),
            RewardOption(100, 15),
            RewardOption(200, 5),
        ),
    ),
    CaseConfig(
        case_id="premium",
        title="🔵 Премиум",
        price=500,
        rewards=(
            RewardOption(250, 50),
            RewardOption(400, 30),
            RewardOption(500, 15),
            RewardOption(1500, 5),
        ),
    ),
    CaseConfig(
        case_id="vip",
        title="🔴 VIP",
        price=1000,
        rewards=(
            RewardOption(500, 50),
            RewardOption(800, 30),
            RewardOption(1000, 15),
            RewardOption(3000, 5),
        ),
    ),
)

CASE_BY_ID = {case.case_id: case for case in CASES}

for configured_case in CASES:
    if sum(reward.probability for reward in configured_case.rewards) != 100:
        raise ValueError(
            f"Вероятности наград для кейса «{configured_case.title}» должны составлять 100%."
        )
    if any(reward.amount < 0 or reward.probability <= 0 for reward in configured_case.rewards):
        raise ValueError("Награды и вероятности должны быть положительными числами.")


def describe_case(case: CaseConfig) -> str:
    rewards = "\n".join(
        f"• {reward.amount} виртуальных ⭐ — {reward.probability}%"
        for reward in case.rewards
    )
    return (
        f"{case.title}\n"
        f"Цена: {case.price} виртуальных ⭐\n"
        f"Возможные награды:\n{rewards}"
    )

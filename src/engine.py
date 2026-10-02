from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

DEFAULT_MAX_PLAYERS = 8


class FlipSevenError(ValueError):
    """Raised when a Flip 7 action is illegal."""


class Phase(str, Enum):
    WAITING = "waiting"
    DEALING = "dealing"
    TURN = "turn"
    ACTION = "action"
    FINISHED = "finished"


STATUS_ACTIVE = "active"
STATUS_STAYED = "stayed"
STATUS_BUSTED = "busted"


@dataclass(frozen=True)
class Card:
    kind: str
    value: Any

    def label(self) -> str:
        """Return a Chinese label for this card."""

        if self.kind == "number":
            return str(self.value)
        if self.kind == "modifier":
            return str(self.value)
        return {
            "freeze": "冰冻",
            "flip_three": "翻三张",
            "second_chance": "第二次机会",
        }.get(str(self.value), str(self.value))


@dataclass
class FlipPlayer:
    user_id: str
    name: str
    numbers: list[int] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    second_chance: bool = False
    status: str = STATUS_ACTIVE
    round_score: int = 0
    total_score: int = 0

    def number_text(self) -> str:
        return "/".join(str(value) for value in self.numbers) or "无"

    def modifier_text(self) -> str:
        return "/".join(self.modifiers) or "无"

    def cards_text(self) -> str:
        parts = [f"数字：{self.number_text()}"]
        if self.modifiers:
            parts.append(f"修正：{self.modifier_text()}")
        if self.second_chance:
            parts.append("持有一张第二次机会")
        return "，".join(parts)


@dataclass
class PendingAction:
    action: str
    actor_index: int
    source: str


@dataclass
class RoundResult:
    lines: list[str]
    game_finished: bool
    winner_ids: list[str]


def build_deck() -> list[Card]:
    """Build the official 94-card Flip 7 deck."""

    deck: list[Card] = []
    deck.append(Card("number", 0))
    for value in range(1, 13):
        deck.extend(Card("number", value) for _ in range(value))
    deck.extend(Card("action", "freeze") for _ in range(3))
    deck.extend(Card("action", "flip_three") for _ in range(3))
    deck.extend(Card("action", "second_chance") for _ in range(3))
    for value in ("+2", "+4", "+6", "+8", "+10", "x2"):
        deck.append(Card("modifier", value))
    return deck


@dataclass
class FlipSevenGame:
    group_id: str
    owner_id: str
    max_players: int = DEFAULT_MAX_PLAYERS
    players: list[FlipPlayer] = field(default_factory=list)
    phase: Phase = Phase.WAITING
    started: bool = False
    deck: list[Card] = field(default_factory=list)
    discard: list[Card] = field(default_factory=list)
    round_no: int = 0
    dealer_index: int = 0
    current_index: int = 0
    initial_deal_index: int = 0
    initial_dealt_count: int = 0
    pending: PendingAction | None = None
    winner_ids: list[str] = field(default_factory=list)
    last_round_scores: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Room management
    # ------------------------------------------------------------------
    def get_player(self, user_id: str) -> FlipPlayer | None:
        return next(
            (player for player in self.players if player.user_id == user_id), None
        )

    def _player_index(self, user_id: str) -> int | None:
        return next(
            (
                index
                for index, player in enumerate(self.players)
                if player.user_id == user_id
            ),
            None,
        )

    def add_player(self, user_id: str, name: str) -> None:
        if self.started or self.phase != Phase.WAITING:
            raise FlipSevenError("游戏已经开始，不能加入。")
        if self.get_player(user_id) is not None:
            raise FlipSevenError("你已经加入本局。")
        if len(self.players) >= self.max_players:
            raise FlipSevenError("房间人数已满。")
        self.players.append(FlipPlayer(user_id=user_id, name=name))

    def remove_player(self, user_id: str) -> None:
        if self.phase != Phase.WAITING:
            raise FlipSevenError("游戏已经开始，不能退出。")
        player = self.get_player(user_id)
        if player is None:
            raise FlipSevenError("你还没有加入本局。")
        if player.user_id == self.owner_id:
            raise FlipSevenError("房主不能退出，请直接结束房间。")
        self.players.remove(player)

    def current_player(self) -> FlipPlayer | None:
        if self.phase not in {Phase.TURN, Phase.ACTION}:
            return None
        if self.phase == Phase.ACTION and self.pending is not None:
            return self.players[self.pending.actor_index]
        if not self.players:
            return None
        return self.players[self.current_index]

    def pending_actor_id(self) -> str | None:
        if self.pending is None:
            return None
        return self.players[self.pending.actor_index].user_id

    def active_indices(self) -> list[int]:
        return [
            index
            for index, player in enumerate(self.players)
            if player.status == STATUS_ACTIVE
        ]

    # ------------------------------------------------------------------
    # Game and round flow
    # ------------------------------------------------------------------
    def start_game(self, rng: random.Random | None = None) -> list[str]:
        if self.phase != Phase.WAITING:
            raise FlipSevenError("游戏已经开始。")
        if len(self.players) < 2:
            raise FlipSevenError("至少需要两名玩家才能开始游戏。")
        rng = rng or random.Random()
        self.players.sort(key=lambda player: player.user_id)
        rng.shuffle(self.players)
        for player in self.players:
            player.total_score = 0
        self.dealer_index = rng.randrange(len(self.players))
        self.round_no = 0
        self.started = True
        self.last_round_scores = {}
        lines = ["翻转七开始。每轮结算后累计积分，不设单局分数上限。"]
        lines.extend(self._start_round(rng))
        return lines

    def _start_round(self, rng: random.Random | None = None) -> list[str]:
        rng = rng or random.Random()
        self.round_no += 1
        self.deck = build_deck()
        rng.shuffle(self.deck)
        self.discard = []
        self.pending = None
        self.winner_ids = []
        for player in self.players:
            player.numbers = []
            player.modifiers = []
            player.second_chance = False
            player.status = STATUS_ACTIVE
            player.round_score = 0
        self.initial_deal_index = self.dealer_index
        self.initial_dealt_count = 0
        self.phase = Phase.DEALING
        lines = [
            f"第 {self.round_no} 轮开始，庄家：{self.players[self.dealer_index].name}。",
        ]
        lines.extend(self._deal_initial_cards())
        return lines

    def _deal_initial_cards(self) -> list[str]:
        lines: list[str] = []
        while self.phase == Phase.DEALING:
            if self.initial_dealt_count >= len(self.players):
                self.phase = Phase.TURN
                self.current_index = self.dealer_index
                if self.players[self.current_index].status != STATUS_ACTIVE:
                    return lines + self._next_turn()
                lines.append(
                    f"初始发牌完成，轮到 {self.players[self.current_index].name} 行动。"
                )
                break
            index = self.initial_deal_index % len(self.players)
            player = self.players[index]
            card = self._draw_card()
            lines.append(f"{player.name} 初始翻到 {card.label()}。")
            result = self._give_card(index, card, source="initial")
            lines.extend(result["lines"])
            if result.get("stop"):
                return lines
            self.initial_deal_index = self._next_index(index)
            self.initial_dealt_count += 1
        return lines

    def _continue_after_pending(self, source: str, actor_index: int) -> list[str]:
        if self.phase == Phase.FINISHED:
            return []
        if source == "initial":
            self.phase = Phase.DEALING
            self.initial_deal_index = self._next_index(actor_index)
            self.initial_dealt_count += 1
            return self._deal_initial_cards()
        return self._next_turn()

    def _draw_card(self) -> Card:
        if not self.deck:
            if self.discard:
                self.deck = self.discard
                self.discard = []
            else:
                self.deck = build_deck()
            random.shuffle(self.deck)
        return self.deck.pop()

    def _give_card(self, player_index: int, card: Card, source: str) -> dict[str, Any]:
        player = self.players[player_index]
        lines: list[str] = []
        if card.kind == "number":
            value = int(card.value)
            if value in player.numbers:
                if player.second_chance:
                    player.second_chance = False
                    self.discard.append(card)
                    lines.append(
                        f"{player.name} 使用第二次机会，抵消了重复的 {value}。"
                    )
                else:
                    player.status = STATUS_BUSTED
                    player.round_score = 0
                    lines.append(f"{player.name} 翻到重复的 {value}，爆掉了。")
                    return {"lines": lines, "stop": False, "bust": True}
            else:
                player.numbers.append(value)
                if len(player.numbers) == 7:
                    lines.append(f"{player.name} 集齐 7 张不同数字，触发翻转七！")
                    lines.extend(self._finish_round())
                    return {"lines": lines, "stop": True, "flip7": True}
        elif card.kind == "modifier":
            player.modifiers.append(str(card.value))
            lines.append(f"{player.name} 获得修正牌 {card.value}。")
        elif card.kind == "action":
            action = str(card.value)
            if action == "second_chance":
                if player.second_chance:
                    target = self._find_second_chance_target(player_index)
                    if target is not None:
                        self.players[target].second_chance = True
                        lines.append(
                            f"{player.name} 已有第二次机会，将新的第二次机会给了"
                            f" {self.players[target].name}。"
                        )
                    else:
                        lines.append(
                            f"{player.name} 已有第二次机会，新的第二次机会作废。"
                        )
                else:
                    player.second_chance = True
                    lines.append(f"{player.name} 获得第二次机会。")
            else:
                self.pending = PendingAction(action, player_index, source)
                self.phase = Phase.ACTION
                effect = {
                    "freeze": "选择一名玩家将其冰冻",
                    "flip_three": "选择一名玩家连翻三张",
                }.get(action, "选择目标")
                lines.append(f"{player.name} 抽到特殊卡 {card.label()}：{effect}。")
                return {"lines": lines, "stop": True, "pending": action}
        return {"lines": lines, "stop": False}

    def _find_second_chance_target(self, exclude_index: int) -> int | None:
        for index in self.active_indices():
            if index != exclude_index and not self.players[index].second_chance:
                return index
        return None

    def hit(self, user_id: str) -> list[str]:
        if self.phase != Phase.TURN:
            raise FlipSevenError("当前不能要牌。")
        player = self.current_player()
        if player is None or player.user_id != user_id:
            raise FlipSevenError("还没有轮到你。")
        index = self.current_index
        card = self._draw_card()
        lines = [f"{player.name} 要牌：{card.label()}。"]
        result = self._give_card(index, card, source="turn")
        lines.extend(result["lines"])
        if result.get("stop"):
            lines.extend(self.ensure_progress())
            return lines
        if result.get("bust"):
            pass
        lines.extend(self._next_turn())
        lines.extend(self.ensure_progress())
        return lines

    def stay(self, user_id: str) -> list[str]:
        if self.phase != Phase.TURN:
            raise FlipSevenError("当前不能停牌。")
        player = self.current_player()
        if player is None or player.user_id != user_id:
            raise FlipSevenError("还没有轮到你。")
        if player.numbers == [0]:
            raise FlipSevenError("手上只有 0 时不能停牌，必须继续要牌。")
        player.status = STATUS_STAYED
        player.round_score = self._score(player)
        lines = [f"{player.name} 停牌，本轮 {player.round_score} 分。"]
        lines.extend(self._next_turn())
        lines.extend(self.ensure_progress())
        return lines

    def resolve_action(self, user_id: str, target_index: int) -> list[str]:
        if self.phase != Phase.ACTION or self.pending is None:
            raise FlipSevenError("当前没有需要选择目标的卡牌。")
        actor = self.players[self.pending.actor_index]
        if actor.user_id != user_id:
            raise FlipSevenError("只有翻到这张牌的玩家可以选择目标。")
        if not 0 <= target_index < len(self.players):
            raise FlipSevenError("目标编号不存在。")
        target = self.players[target_index]
        if target.status != STATUS_ACTIVE:
            raise FlipSevenError("不能选择已经停牌或爆掉的玩家。")
        pending = self.pending
        action = pending.action
        action_label = {
            "freeze": "冰冻",
            "flip_three": "翻三张",
        }.get(action, action)
        pending_round = self.round_no
        self.pending = None
        lines = [f"{actor.name} 对 {target.name} 使用 {action_label}。"]
        if action == "freeze":
            target.status = STATUS_STAYED
            target.round_score = self._score(target)
            lines.append(f"{target.name} 被冰冻，本轮 {target.round_score} 分。")
            lines.extend(
                self._continue_after_pending(pending.source, pending.actor_index)
            )
            return lines
        if action == "flip_three":
            lines.extend(self._apply_flip_three(target_index))
            if self.round_no == pending_round:
                lines.extend(
                    self._continue_after_pending(pending.source, pending.actor_index)
                )
            return lines
        raise FlipSevenError("未知行动卡。")

    def _apply_flip_three(self, target_index: int, depth: int = 0) -> list[str]:
        lines: list[str] = []
        target = self.players[target_index]
        for _ in range(3):
            if target.status != STATUS_ACTIVE:
                break
            card = self._draw_card()
            lines.append(f"{target.name} 翻三张：{card.label()}。")
            if card.kind == "action":
                action = str(card.value)
                if action == "second_chance":
                    if target.second_chance:
                        other = self._find_second_chance_target(target_index)
                        if other is not None:
                            self.players[other].second_chance = True
                            lines.append(f"第二次机会转给 {self.players[other].name}。")
                        else:
                            lines.append("第二次机会作废。")
                    else:
                        target.second_chance = True
                        lines.append(f"{target.name} 获得第二次机会。")
                elif action == "freeze":
                    target.status = STATUS_STAYED
                    target.round_score = self._score(target)
                    lines.append(
                        f"{target.name} 被冰冻，本轮 {target.round_score} 分。"
                    )
                    break
                elif action == "flip_three":
                    if depth < 2:
                        lines.append(f"{target.name} 再次翻三张。")
                        lines.extend(self._apply_flip_three(target_index, depth + 1))
                    else:
                        lines.append("连续翻三张次数过多，本次忽略。")
                continue
            result = self._give_card(target_index, card, source="flip_three")
            lines.extend(result["lines"])
            if result.get("stop") or result.get("bust"):
                break
        return lines

    def ensure_progress(self) -> list[str]:
        """Recover from any phase anomaly before the game waits forever."""

        if self.phase == Phase.ACTION and self.pending is None:
            self.phase = Phase.TURN
            return self._next_turn()
        if self.phase == Phase.TURN and self.current_player() is None:
            return self._next_turn()
        return []

    def _next_turn(self) -> list[str]:
        if self.phase == Phase.FINISHED:
            return []
        active = self.active_indices()
        if not active:
            return self._finish_round()
        for offset in range(1, len(self.players) + 1):
            index = (self.current_index + offset) % len(self.players)
            if self.players[index].status == STATUS_ACTIVE:
                self.current_index = index
                self.phase = Phase.TURN
                return [f"轮到 {self.players[index].name} 行动。"]
        self.phase = Phase.TURN
        return []

    def _finish_round(self) -> list[str]:
        lines: list[str] = ["本轮结束。"]
        for player in self.players:
            if player.status == STATUS_BUSTED:
                player.round_score = 0
            elif player.status != STATUS_STAYED:
                player.round_score = self._score(player)
            player.total_score += player.round_score
        self.last_round_scores = {
            player.user_id: player.round_score for player in self.players
        }
        for player in self.players:
            lines.append(
                f"{player.name}：本轮 {player.round_score} 分，累计 {player.total_score} 分。"
            )
        self.dealer_index = self._next_index(self.dealer_index)
        lines.append("累计积分已结算，开始下一轮。")
        lines.extend(self._start_round())
        return lines

    def _score(self, player: FlipPlayer) -> int:
        if player.status == STATUS_BUSTED:
            return 0
        value = sum(player.numbers)
        if "x2" in player.modifiers:
            value *= 2
        for modifier in player.modifiers:
            if modifier.startswith("+"):
                try:
                    value += int(modifier[1:])
                except ValueError:
                    pass
        if len(player.numbers) == 7:
            value += 15
        return value

    def _next_index(self, index: int) -> int:
        return (index + 1) % len(self.players)

    def status_lines(self) -> list[str]:
        dealer = self.players[self.dealer_index].name if self.players else "无"
        lines = [
            f"阶段：{self._phase_label()}",
            f"第 {self.round_no} 轮，庄家：{dealer}。",
        ]
        if self.phase == Phase.DEALING:
            lines.append("正在初始发牌。")
        if self.phase == Phase.TURN and self.players:
            actor = self.current_player()
            if actor is not None:
                lines.append(
                    f"当前行动：{actor.name}，可发送“翻转七要牌”或“翻转七停牌”。"
                )
                if actor.numbers == [0]:
                    lines.append(f"{actor.name} 手上只有 0，必须继续要牌。")
        if self.phase == Phase.ACTION and self.pending is not None:
            actor = self.players[self.pending.actor_index]
            action = {
                "freeze": "冰冻",
                "flip_three": "翻三张",
            }.get(self.pending.action, self.pending.action)
            lines.append(f"{actor.name} 抽到 {action}，需要选择目标：")
            for index, player in enumerate(self.players, start=1):
                if player.status == STATUS_ACTIVE:
                    lines.append(f"{index}. {player.name}")
        lines.append("玩家：")
        for player in self.players:
            status = {
                STATUS_ACTIVE: "行动中",
                STATUS_STAYED: "已停牌",
                STATUS_BUSTED: "已爆掉",
            }.get(player.status, player.status)
            lines.append(
                f"- {player.name}：{status}，{player.cards_text()}，"
                f"本轮 {player.round_score} 分，累计 {player.total_score} 分。"
            )
        return lines

    def _phase_label(self) -> str:
        return {
            Phase.WAITING: "等待加入",
            Phase.DEALING: "初始发牌",
            Phase.TURN: "玩家行动",
            Phase.ACTION: "选择目标",
            Phase.FINISHED: "已结束",
        }.get(self.phase, self.phase.value)

    def to_summary(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "owner_id": self.owner_id,
            "phase": self.phase.value,
            "round_no": self.round_no,
            "players": [
                {
                    "user_id": player.user_id,
                    "name": player.name,
                    "numbers": list(player.numbers),
                    "modifiers": list(player.modifiers),
                    "second_chance": player.second_chance,
                    "status": player.status,
                    "round_score": player.round_score,
                    "total_score": player.total_score,
                }
                for player in self.players
            ],
            "winner_ids": list(self.winner_ids),
        }

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from .src.engine import (
        DEFAULT_MAX_PLAYERS,
        TARGET_SCORE,
        FlipSevenError,
        FlipSevenGame,
        Phase,
    )
    from .src.qqofficial import (
        ButtonSpec,
        extract_context,
        is_qqofficial_event,
        send_group_reply,
    )
except ImportError:  # pragma: no cover - direct local import fallback
    from src.engine import (
        DEFAULT_MAX_PLAYERS,
        TARGET_SCORE,
        FlipSevenError,
        FlipSevenGame,
        Phase,
    )
    from src.qqofficial import (
        ButtonSpec,
        extract_context,
        is_qqofficial_event,
        send_group_reply,
    )


PLUGIN_NAME = "astrbot_plugin_flip_seven"


@dataclass
class CommandOutcome:
    """Result of one Flip 7 command."""

    text: str
    game: FlipSevenGame | None = None
    buttons: list[ButtonSpec] | None = None
    error: bool = False


@register(
    PLUGIN_NAME,
    "Codex",
    "QQ 官方群聊翻转七：多人卡牌游戏，支持要牌、停牌、行动卡目标和 200 分结算。",
    "1.0.0",
)
class FlipSevenPlugin(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.config = dict(config) if config else {}
        self.max_players = self._config_int(
            "max_players", DEFAULT_MAX_PLAYERS, minimum=2, maximum=20
        )
        self.target_score = self._config_int("target_score", TARGET_SCORE, minimum=50)
        self.games: dict[str, FlipSevenGame] = {}
        self.group_locks: dict[str, asyncio.Lock] = {}

    async def initialize(self) -> None:
        logger.info("[FlipSeven] initialized")

    async def terminate(self) -> None:
        self.games.clear()
        self.group_locks.clear()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    @filter.command("翻转七菜单", alias={"翻转七帮助", "翻转七"})
    async def menu_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "menu"):
            yield result
        event.stop_event()

    @filter.command("翻转七创建", alias={"翻转七开局"})
    async def create_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "create"):
            yield result
        event.stop_event()

    @filter.command("翻转七加入", alias={"翻转七报名"})
    async def join_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "join"):
            yield result
        event.stop_event()

    @filter.command("翻转七退出", alias={"翻转七离开"})
    async def leave_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "leave"):
            yield result
        event.stop_event()

    @filter.command("翻转七开始", alias={"翻转七发牌"})
    async def start_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "start"):
            yield result
        event.stop_event()

    @filter.command("翻转七看", alias={"翻转七状态"})
    async def status_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "status"):
            yield result
        event.stop_event()

    @filter.command("翻转七要牌", alias={"翻七要牌", "flip7hit"})
    async def hit_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "hit"):
            yield result
        event.stop_event()

    @filter.command("翻转七停牌", alias={"翻七停牌", "flip7stay"})
    async def stay_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "stay"):
            yield result
        event.stop_event()

    @filter.command("翻转七选择", alias={"翻转七指定"})
    async def select_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "select"):
            yield result
        event.stop_event()

    @filter.command("翻转七结束", alias={"翻转七取消"})
    async def end_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "end"):
            yield result
        event.stop_event()

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    async def _handle_command(self, event: AstrMessageEvent, command: str):
        group_id, user_id, name = self._identity(event)
        if not group_id:
            yield event.plain_result("翻转七只能在群聊中使用。")
            return
        lock = self.group_locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            try:
                outcome = await self._execute_command(
                    event, group_id, user_id, name, command
                )
            except FlipSevenError as exc:
                outcome = CommandOutcome(text=str(exc), error=True)
            except Exception as exc:  # noqa: BLE001 - isolate one group
                logger.exception("[FlipSeven] command %s failed: %s", command, exc)
                outcome = CommandOutcome(text=f"翻转七处理失败：{exc}", error=True)

        if outcome.error:
            yield event.plain_result(outcome.text)
            return
        buttons = outcome.buttons
        if buttons is None and outcome.game is not None:
            buttons = self._game_buttons(outcome.game)
        if await self._try_send_qqofficial(event, outcome.text, buttons):
            return
        if outcome.text:
            yield event.plain_result(outcome.text)

    async def _execute_command(
        self,
        event: AstrMessageEvent,
        group_id: str,
        user_id: str,
        name: str,
        command: str,
    ) -> CommandOutcome:
        if command == "menu":
            return self._menu_outcome()
        if command == "create":
            return self._create_game(group_id, user_id, name)
        if command == "join":
            return self._join_game(group_id, user_id, name)
        if command == "leave":
            return self._leave_game(group_id, user_id)
        if command == "start":
            return self._start_game(event, group_id, user_id)
        if command == "status":
            return self._show_status(group_id)
        if command == "hit":
            return self._hit(group_id, user_id)
        if command == "stay":
            return self._stay(group_id, user_id)
        if command == "select":
            return self._select_target(group_id, user_id, self._message_text(event))
        if command == "end":
            return self._end_game(event, group_id, user_id)
        raise FlipSevenError("未知指令。")

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------
    def _create_game(self, group_id: str, user_id: str, name: str) -> CommandOutcome:
        existing = self.games.get(group_id)
        if existing is not None and existing.phase != Phase.FINISHED:
            raise FlipSevenError("本群已经有一局翻转七正在进行。")
        game = FlipSevenGame(
            group_id=group_id,
            owner_id=user_id,
            max_players=self.max_players,
            target_score=self.target_score,
        )
        game.add_player(user_id, name)
        self.games[group_id] = game
        return CommandOutcome(
            text=(
                "翻转七房间已创建。\n"
                f"人数：{len(game.players)}/{game.max_players}。\n"
                f"目标分数：{game.target_score} 分。"
            ),
            game=game,
        )

    def _join_game(self, group_id: str, user_id: str, name: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None or game.phase != Phase.WAITING:
            raise FlipSevenError("当前没有等待加入的翻转七房间。")
        game.add_player(user_id, name)
        return CommandOutcome(
            text=f"{name} 已加入，当前 {len(game.players)}/{game.max_players} 人。",
            game=game,
        )

    def _leave_game(self, group_id: str, user_id: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None or game.phase != Phase.WAITING:
            raise FlipSevenError("当前没有等待加入的翻转七房间。")
        game.remove_player(user_id)
        return CommandOutcome(text="已退出等待房间。", game=game)

    def _start_game(
        self, event: AstrMessageEvent, group_id: str, user_id: str
    ) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            raise FlipSevenError("当前没有翻转七房间。")
        if game.phase != Phase.WAITING:
            raise FlipSevenError("游戏已经开始。")
        if user_id != game.owner_id and not self._is_admin(event):
            raise FlipSevenError("只有房主或管理员可以开始游戏。")
        lines = game.start_game(random.Random())
        return CommandOutcome(text="\n".join(lines), game=game)

    def _show_status(self, group_id: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            raise FlipSevenError("当前没有翻转七房间。")
        return CommandOutcome(text="\n".join(game.status_lines()), game=game)

    def _hit(self, group_id: str, user_id: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            raise FlipSevenError("当前没有翻转七房间。")
        lines = game.hit(user_id)
        return CommandOutcome(text="\n".join(lines), game=game)

    def _stay(self, group_id: str, user_id: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            raise FlipSevenError("当前没有翻转七房间。")
        lines = game.stay(user_id)
        return CommandOutcome(text="\n".join(lines), game=game)

    def _select_target(self, group_id: str, user_id: str, text: str) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            raise FlipSevenError("当前没有翻转七房间。")
        match = re.search(r"\d+", str(text or ""))
        if match is None:
            raise FlipSevenError("请选择目标编号。")
        target_index = int(match.group(0)) - 1
        lines = game.resolve_action(user_id, target_index)
        return CommandOutcome(text="\n".join(lines), game=game)

    def _end_game(
        self, event: AstrMessageEvent, group_id: str, user_id: str
    ) -> CommandOutcome:
        game = self.games.get(group_id)
        if game is None:
            raise FlipSevenError("当前没有翻转七房间。")
        if user_id != game.owner_id and not self._is_admin(event):
            raise FlipSevenError("只有房主或管理员可以结束房间。")
        self.games.pop(group_id, None)
        return CommandOutcome(text="翻转七房间已结束。", game=None, buttons=[])

    # ------------------------------------------------------------------
    # Buttons and rendering
    # ------------------------------------------------------------------
    def _menu_outcome(self) -> CommandOutcome:
        text = (
            "翻转七帮助\n\n"
            "1. 目标：先达到目标分数，默认 200 分。\n"
            "2. 每轮开始庄家给每名玩家翻一张牌。\n"
            "3. 轮到你时可以发送“要牌”或“停牌”。\n"
            "4. 翻到重复数字会爆掉；有第二次机会可以抵消一次。\n"
            "5. 数字牌不重复时继续累计；7 张不同数字触发翻转七，+15 分。\n"
            "6. 修正牌 +2/+4/+6/+8/+10 加在数字和上，x2 先翻倍再加其他修正。\n"
            "7. 冰冻让目标直接停牌结算；翻三张让目标连翻三张。\n"
            "8. 行动卡需要发送“翻转七选择 编号”选择目标。\n\n"
            "命令：翻转七创建 / 翻转七加入 / 翻转七开始 / 翻转七看 / "
            "翻转七要牌 / 翻转七停牌 / 翻转七选择 / 翻转七结束"
        )
        return CommandOutcome(text=text, buttons=self._menu_buttons())

    def _menu_buttons(self) -> list[ButtonSpec]:
        return [
            ButtonSpec("f7_menu_create", "创建房间", "翻转七创建"),
            ButtonSpec("f7_menu_join", "加入", "翻转七加入"),
            ButtonSpec("f7_menu_start", "开始", "翻转七开始"),
            ButtonSpec("f7_menu_status", "状态", "翻转七看"),
            ButtonSpec("f7_menu_help", "翻转七帮助", "翻转七帮助"),
            ButtonSpec("f7_menu_end", "结束", "翻转七结束"),
        ]

    def _game_buttons(self, game: FlipSevenGame | None) -> list[ButtonSpec]:
        if game is None or game.phase == Phase.FINISHED:
            return []
        if game.phase == Phase.WAITING:
            return [
                ButtonSpec("f7_wait_join", "加入", "翻转七加入"),
                ButtonSpec(
                    "f7_wait_start", "开始", "翻转七开始", only_for=game.owner_id
                ),
                ButtonSpec("f7_wait_status", "状态", "翻转七看"),
                ButtonSpec("f7_wait_help", "翻转七帮助", "翻转七帮助"),
                ButtonSpec("f7_wait_end", "结束", "翻转七结束", only_for=game.owner_id),
            ]
        if game.phase == Phase.ACTION and game.pending is not None:
            actor = game.pending_actor_id()
            buttons: list[ButtonSpec] = []
            for index, player in enumerate(game.players, start=1):
                if player.status != "active":
                    continue
                buttons.append(
                    ButtonSpec(
                        f"f7_target_{index}",
                        f"{index}. {player.name}",
                        f"翻转七选择 {index}",
                        only_for=actor,
                    )
                )
            buttons.extend(
                [
                    ButtonSpec("f7_action_status", "状态", "翻转七看"),
                    ButtonSpec("f7_action_help", "翻转七帮助", "翻转七帮助"),
                    ButtonSpec(
                        "f7_action_end", "结束", "翻转七结束", only_for=game.owner_id
                    ),
                ]
            )
            return buttons
        buttons = []
        if game.phase == Phase.TURN:
            actor = game.current_player()
            if actor is not None:
                buttons.extend(
                    [
                        ButtonSpec(
                            "f7_act_hit", "要牌", "要牌", only_for=actor.user_id
                        ),
                        ButtonSpec(
                            "f7_act_stay", "停牌", "停牌", only_for=actor.user_id
                        ),
                    ]
                )
        buttons.extend(
            [
                ButtonSpec("f7_play_status", "状态", "翻转七看"),
                ButtonSpec("f7_play_help", "翻转七帮助", "翻转七帮助"),
                ButtonSpec("f7_play_end", "结束", "翻转七结束", only_for=game.owner_id),
            ]
        )
        return buttons

    # ------------------------------------------------------------------
    # Platform helpers
    # ------------------------------------------------------------------
    def _identity(self, event: AstrMessageEvent) -> tuple[str, str, str]:
        group_id = str(event.get_group_id() or "")
        user_id = str(event.get_sender_id() or "")
        name = str(event.get_sender_name() or "") or f"玩家_{user_id[-6:]}"
        return group_id, user_id, name

    async def _try_send_qqofficial(
        self, event: AstrMessageEvent, text: str, buttons: list[ButtonSpec] | None
    ) -> bool:
        if not is_qqofficial_event(event):
            return False
        context = extract_context(event)
        if context is None:
            return False
        return await send_group_reply(event, context, text, buttons or [])

    def _message_text(self, event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_message_str", None)
        if callable(getter):
            return str(getter() or "")
        return str(getattr(event, "message_str", "") or "")

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:  # noqa: BLE001 - compatibility with test doubles
            return False

    def _config_int(
        self,
        key: str,
        default: int,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        raw = self.config.get(key, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

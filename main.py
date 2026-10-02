from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

try:
    from .src.engine import (
        DEFAULT_MAX_PLAYERS,
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
    "QQ 官方群聊翻转七：多人卡牌游戏，支持要牌、停牌、行动卡目标、每轮积分结算和群排行榜。",
    "1.2.0",
)
class FlipSevenPlugin(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.config = dict(config) if config else {}
        self.max_players = self._config_int(
            "max_players", DEFAULT_MAX_PLAYERS, minimum=2, maximum=20
        )
        self.leaderboard_path = (
            Path(StarTools.get_data_dir(PLUGIN_NAME)) / "leaderboard.json"
        )
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
    @filter.command("翻转七菜单", alias={"翻转七"})
    async def menu_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "menu"):
            yield result
        event.stop_event()

    @filter.command("翻转七帮助")
    async def help_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "help"):
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

    @filter.command("翻转七要牌")
    async def hit_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "hit"):
            yield result
        event.stop_event()

    @filter.command("翻转七停牌")
    async def stay_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "stay"):
            yield result
        event.stop_event()

    @filter.command("翻转七选择", alias={"翻转七指定"})
    async def select_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "select"):
            yield result
        event.stop_event()

    @filter.command("翻转七排行榜", alias={"翻转七积分榜", "翻转七积分"})
    async def leaderboard_command(self, event: AstrMessageEvent):
        async for result in self._handle_command(event, "leaderboard"):
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
        if outcome.game is not None:
            self._sync_leaderboard(outcome.game)
            if outcome.game.phase == Phase.FINISHED:
                self.games.pop(group_id, None)
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
        if command == "help":
            return self._help_outcome()
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
        if command == "leaderboard":
            return self._leaderboard_outcome(group_id)
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
        )
        game.add_player(user_id, name)
        self.games[group_id] = game
        return CommandOutcome(
            text=(
                f"翻转七房间已创建。\n人数：{len(game.players)}/{game.max_players}。"
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

    # ------------------------------------------------------------------
    # Persistent leaderboard
    # ------------------------------------------------------------------
    def _load_leaderboard(self) -> dict[str, dict[str, dict[str, Any]]]:
        if not self.leaderboard_path.exists():
            return {}
        try:
            data = json.loads(self.leaderboard_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_leaderboard(self, data: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.leaderboard_path.parent.mkdir(parents=True, exist_ok=True)
        self.leaderboard_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _sync_leaderboard(self, game: FlipSevenGame) -> None:
        if not game.last_round_scores:
            return
        data = self._load_leaderboard()
        group = data.setdefault(game.group_id, {})
        for player in game.players:
            delta = int(game.last_round_scores.get(player.user_id, 0))
            entry = group.setdefault(player.user_id, {"name": player.name, "score": 0})
            entry["name"] = player.name
            entry["score"] = int(entry.get("score", 0)) + delta
        self._save_leaderboard(data)
        game.last_round_scores.clear()

    def _leaderboard_outcome(self, group_id: str) -> CommandOutcome:
        data = self._load_leaderboard().get(group_id, {})
        if not data:
            return CommandOutcome(
                text="本群暂无翻转七积分记录。", buttons=self._menu_buttons()
            )
        rows = sorted(
            data.items(),
            key=lambda item: int(item[1].get("score", 0)),
            reverse=True,
        )[:20]
        lines = ["翻转七积分排行榜："]
        for index, (user_id, entry) in enumerate(rows, start=1):
            name = str(entry.get("name") or user_id)
            score = int(entry.get("score", 0))
            lines.append(f"{index}. {name}：{score} 分")
        return CommandOutcome(text="\n".join(lines), buttons=self._menu_buttons())

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
        return CommandOutcome(text="翻转七菜单", buttons=self._menu_buttons())

    def _help_outcome(self) -> CommandOutcome:
        text = (
            "翻转七帮助\n\n"
            "1. 使用官方 94 张牌：数字牌 79 张、行动牌 9 张、修正牌 6 张。\n"
            "2. 每轮庄家给每名玩家翻一张初始牌，然后从庄家开始轮流行动。\n"
            "3. 轮到你时发送“翻转七要牌”或“翻转七停牌”。\n"
            "4. 抽到 0 的玩家本轮不能停牌，必须继续要牌。\n"
            "5. 翻到重复数字会爆掉，本轮 0 分；有第二次机会可抵消一次。\n"
            "6. 集齐 7 张不同数字立即触发翻转七，本轮额外 +15 分。\n"
            "7. 修正牌：+2/+4/+6/+8/+10 加在数字总和上；x2 先翻倍再加其他修正。\n"
            "8. 冰冻让目标立即停牌结算；翻三张让目标连续翻三张。\n"
            "9. 抽到行动卡后发送“翻转七选择 编号”选择目标。\n"
            "10. 每局只有一轮，一局定胜负；本轮积分会累计到群排行榜。\n"
            "11. 发送“翻转七排行榜”查看群内累计积分排行。\n\n"
            "命令：翻转七菜单 / 翻转七创建 / 翻转七加入 / 翻转七开始 / "
            "翻转七看 / 翻转七要牌 / 翻转七停牌 / 翻转七选择 / "
            "翻转七排行榜 / 翻转七结束"
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
                buttons.append(
                    ButtonSpec(
                        "f7_act_hit", "要牌", "翻转七要牌", only_for=actor.user_id
                    )
                )
                if 0 not in actor.numbers:
                    buttons.append(
                        ButtonSpec(
                            "f7_act_stay", "停牌", "翻转七停牌", only_for=actor.user_id
                        )
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

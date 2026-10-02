from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

_SPEC = importlib.util.spec_from_file_location(
    "flip_seven_plugin_main", PLUGIN_DIR / "main.py"
)
assert _SPEC is not None and _SPEC.loader is not None
plugin_main = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = plugin_main
_SPEC.loader.exec_module(plugin_main)


class FakeAPI:
    def __init__(self) -> None:
        self.group_messages: list[dict] = []

    async def post_group_message(self, **payload):
        self.group_messages.append(payload)
        return {"id": str(len(self.group_messages))}


class FakeBot:
    def __init__(self) -> None:
        self.api = FakeAPI()


class FakeRawMessage:
    def __init__(self, group_openid: str, member_openid: str) -> None:
        self.group_openid = group_openid
        self.id = "message-id"
        self.msg_seq = 1
        self.author = SimpleNamespace(member_openid=member_openid)


class FakeEvent:
    def __init__(self, member_openid: str, name: str) -> None:
        self.bot = FakeBot()
        self.raw = FakeRawMessage("group-id", member_openid)
        self.message_obj = SimpleNamespace(
            raw_message=self.raw, message_id="message-id"
        )
        self.message_str = ""
        self._name = name

    def get_platform_name(self) -> str:
        return "qq_official"

    def get_platform_id(self) -> str:
        return "qq_official"

    def get_group_id(self) -> str:
        return self.raw.group_openid

    def get_sender_id(self) -> str:
        return self.raw.author.member_openid

    def get_sender_name(self) -> str:
        return self._name

    def get_message_str(self) -> str:
        return self.message_str

    def is_admin(self) -> bool:
        return False

    def plain_result(self, text: str) -> str:
        return text

    def stop_event(self) -> None:
        return None


def run(coro):
    return asyncio.run(coro)


async def collect(asyncgen) -> list:
    return [item async for item in asyncgen]


def test_create_join_start_flow() -> None:
    plugin = plugin_main.FlipSevenPlugin(
        context=SimpleNamespace(),
        config={"max_players": 4},
    )
    owner = FakeEvent("owner", "房主")
    guest = FakeEvent("guest", "玩家二")

    run(collect(plugin.create_command(owner)))
    run(collect(plugin.join_command(guest)))
    run(collect(plugin.start_command(owner)))

    assert "group-id" in plugin.games
    game = plugin.games["group-id"]
    assert len(game.players) == 2
    assert game.phase != plugin_main.Phase.WAITING
    assert owner.bot.api.group_messages


def test_menu_is_buttons_only_and_help_contains_rules() -> None:
    plugin = plugin_main.FlipSevenPlugin(context=SimpleNamespace(), config={})
    menu = plugin._menu_outcome()
    help_outcome = plugin._help_outcome()
    assert menu.text == "翻转七菜单"
    assert "官方 94 张牌" in help_outcome.text
    assert "抽到 0" in help_outcome.text
    labels = [button.label for button in plugin._menu_buttons()]
    assert "翻转七帮助" in labels


def test_zero_player_has_no_stay_button() -> None:
    plugin = plugin_main.FlipSevenPlugin(context=SimpleNamespace(), config={})
    game = plugin_main.FlipSevenGame("group-id", "owner", max_players=4)
    game.add_player("owner", "房主")
    game.add_player("guest", "玩家二")
    game.started = True
    game.phase = plugin_main.Phase.TURN
    game.current_index = 0
    game.players[0].numbers = [0, 5]
    labels = [button.label for button in plugin._game_buttons(game)]
    assert "要牌" in labels
    assert "停牌" not in labels

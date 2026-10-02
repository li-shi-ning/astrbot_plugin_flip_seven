from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from src.engine import (  # noqa: E402
    STATUS_ACTIVE,
    STATUS_BUSTED,
    STATUS_STAYED,
    Card,
    FlipSevenGame,
    build_deck,
)


def make_game() -> FlipSevenGame:
    game = FlipSevenGame("g", "a", max_players=4, target_score=200)
    game.add_player("a", "A")
    game.add_player("b", "B")
    return game


def test_official_deck_has_94_cards() -> None:
    deck = build_deck()
    assert len(deck) == 94
    assert len([card for card in deck if card.kind == "number"]) == 79
    assert len([card for card in deck if card.kind == "action"]) == 9
    assert len([card for card in deck if card.kind == "modifier"]) == 6


def test_duplicate_number_busts_without_second_chance() -> None:
    game = make_game()
    player = game.players[0]
    result = game._give_card(0, Card("number", 5), "turn")
    assert result.get("bust") is not True
    result = game._give_card(0, Card("number", 5), "turn")
    assert result["bust"] is True
    assert player.status == STATUS_BUSTED
    assert player.round_score == 0


def test_second_chance_cancels_duplicate() -> None:
    game = make_game()
    player = game.players[0]
    player.second_chance = True
    game._give_card(0, Card("number", 7), "turn")
    result = game._give_card(0, Card("number", 7), "turn")
    assert "bust" not in result
    assert player.status == STATUS_ACTIVE
    assert player.second_chance is False
    assert player.numbers == [7]


def test_score_applies_x2_and_flat_modifiers() -> None:
    game = make_game()
    player = game.players[0]
    player.numbers = [3, 5, 7]
    player.modifiers = ["+4", "x2"]
    assert game._score(player) == (3 + 5 + 7) * 2 + 4


def test_flip_seven_bonus() -> None:
    game = make_game()
    player = game.players[0]
    player.numbers = [1, 2, 3, 4, 5, 6, 7]
    assert game._score(player) == 1 + 2 + 3 + 4 + 5 + 6 + 7 + 15


def test_stay_banks_round_score() -> None:
    game = make_game()
    player = game.players[0]
    game.phase = __import__("src.engine", fromlist=["Phase"]).Phase.TURN
    game.current_index = 0
    player.numbers = [2, 4]
    lines = game.stay(player.user_id)
    assert player.status == STATUS_STAYED
    assert player.round_score == 6
    assert any("停牌" in line for line in lines)

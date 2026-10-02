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
    game = FlipSevenGame("g", "a", max_players=4)
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


def test_room_lock_rejects_join_after_start() -> None:
    game = make_game()
    game.start_game()
    try:
        game.add_player("c", "C")
    except Exception as exc:
        assert "不能加入" in str(exc)
    else:  # pragma: no cover - guard against regression
        raise AssertionError("join after start should be rejected")


def test_zero_blocks_stay_even_with_other_numbers() -> None:
    game = make_game()
    player = game.players[0]
    game.phase = __import__("src.engine", fromlist=["Phase"]).Phase.TURN
    game.current_index = 0
    player.numbers = [0, 5, 8]
    try:
        game.stay(player.user_id)
    except Exception as exc:
        assert "抽到 0" in str(exc)
    else:  # pragma: no cover - guard against regression
        raise AssertionError("player with a 0 should not be allowed to stay")


def test_initial_flip_three_with_nested_flip_three_does_not_stuck() -> None:
    game = make_game()
    game.phase = __import__("src.engine", fromlist=["Phase"]).Phase.DEALING
    game.round_no = 1
    game.dealer_index = 0
    game.current_index = 0
    game.initial_deal_index = 0
    game.initial_dealt_count = 0
    game.deck = [
        Card("number", 4),
        Card("number", 3),
        Card("number", 2),
        Card("number", 1),
        Card("action", "flip_three"),
        Card("action", "flip_three"),
    ]
    game._deal_initial_cards()
    assert game.phase.name == "ACTION"
    game.resolve_action("a", 0)
    assert game.pending is None
    assert game.phase.name != "ACTION"


def test_flip_three_other_target_cards_do_not_stuck() -> None:
    scenarios = [
        ["freeze", "number", "number"],
        ["flip_three", "freeze", "number"],
        ["flip_three", "flip_three", "freeze"],
        ["second_chance", "freeze", "number"],
    ]
    for source in ("initial", "turn"):
        for scenario in scenarios:
            game = make_game()
            game.started = True
            game.phase = __import__("src.engine", fromlist=["Phase"]).Phase.ACTION
            game.round_no = 1
            game.dealer_index = 0
            game.current_index = 0
            game.initial_deal_index = 0
            game.initial_dealt_count = 0
            game.pending = __import__(
                "src.engine", fromlist=["PendingAction"]
            ).PendingAction("flip_three", 0, source)
            cards = []
            for index, value in enumerate(scenario):
                if value == "number":
                    cards.append(Card("number", index + 1))
                else:
                    cards.append(Card("action", value))
            game.deck = list(reversed(cards)) + [
                Card("number", 8),
                Card("number", 9),
                Card("number", 10),
                Card("number", 11),
            ]
            game.discard = []

            game.resolve_action("a", 0)
            game.ensure_progress()
            assert not (game.phase.name == "ACTION" and game.pending is None)


def test_first_round_starts_next_round_and_records_scores() -> None:
    game = make_game()
    game.started = True
    game.total_rounds = 3
    game.round_no = 1
    game.phase = __import__("src.engine", fromlist=["Phase"]).Phase.TURN
    game.players[0].numbers = [1, 2]
    game.players[0].status = STATUS_STAYED
    game.players[0].round_score = 3
    game.players[1].numbers = [5]
    game.players[1].status = STATUS_BUSTED
    lines = game._finish_round()
    assert game.phase.name != "FINISHED"
    assert game.round_no == 2
    assert game.last_round_scores[game.players[0].user_id] == 3
    assert game.last_round_scores[game.players[1].user_id] == 0
    assert any("第 2/3 轮" in line for line in lines)


def test_third_round_finishes_game_by_total_score() -> None:
    game = make_game()
    game.started = True
    game.total_rounds = 3
    game.round_no = 3
    game.phase = __import__("src.engine", fromlist=["Phase"]).Phase.TURN
    game.players[0].numbers = [1, 2]
    game.players[0].status = STATUS_STAYED
    game.players[0].round_score = 3
    game.players[0].total_score = 20
    game.players[1].numbers = [5]
    game.players[1].status = STATUS_BUSTED
    game.players[1].total_score = 10
    lines = game._finish_round()
    assert game.phase.name == "FINISHED"
    assert game.winner_ids == [game.players[0].user_id]
    assert game.last_round_scores[game.players[0].user_id] == 3
    assert game.last_round_scores[game.players[1].user_id] == 0
    assert any("总积分 23 分获胜" in line for line in lines)

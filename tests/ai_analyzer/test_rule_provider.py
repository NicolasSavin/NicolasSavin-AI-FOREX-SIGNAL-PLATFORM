from app.services.ai_analyzer import RuleBasedAnalyzerProvider


def test_rule_based_provider_extracts_full_trading_idea() -> None:
    transcript = (
        "На H1 EURUSD bullish setup после sweep liquidity. "
        "Buy entry 1.1745, stop loss 1.1680, target1 1.1830, target2 1.1900. "
        "Подтверждение через OrderFlow, VWAP и Volume Profile."
    )

    review = RuleBasedAnalyzerProvider().analyze(transcript, {"video_id": "video-1"})

    assert review.video_id == "video-1"
    assert review.symbol == "EURUSD"
    assert review.timeframe == "H1"
    assert review.direction == "BUY"
    assert review.entry == 1.1745
    assert review.stop_loss == 1.1680
    assert review.take_profit == 1.1830
    assert review.targets == [1.1830, 1.1900]
    assert "VWAP" in review.mentioned_indicators
    assert "OrderFlow" in review.mentioned_indicators
    assert review.confidence >= 80
    assert review.summary


def test_rule_based_provider_keeps_low_confidence_when_transcript_is_sparse() -> None:
    review = RuleBasedAnalyzerProvider().analyze("Обзор рынка без конкретной сделки.", {"video_id": "empty"})

    assert review.video_id == "empty"
    assert review.entry is None
    assert review.confidence < 30
    assert review.risks

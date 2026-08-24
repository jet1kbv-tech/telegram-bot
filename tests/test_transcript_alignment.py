from bot.services.polza_master_transcription import MasterTranscriptionResult
from bot.services.transcript_alignment import align_transcript, select_best_transcript
from bot.services.transcript_processing import cleanup_best_effort
from bot.services.transcript_processing import TranscriptTurn


def turns(*texts):
    return [TranscriptTurn(i, f"Спикер {i}", i * 10.0, i * 10.0 + 9, f"00:00:{i * 10:02d}", text)
            for i, text in enumerate(texts, 1)]


def assert_hybrid(source, master, expected):
    original = turns(*source)
    result = align_transcript(original, master)
    assert result.accepted, result.rejection_reason
    assert [turn.text for turn in result.turns] == expected
    assert [(t.speaker, t.start, t.end, t.timestamp) for t in result.turns] == [
        (t.speaker, t.start, t.end, t.timestamp) for t in original]
    master_words = " ".join(master.casefold().replace(",", "").replace("?", "").replace(".", "").split())
    output_words = " ".join(" ".join(t.text for t in result.turns).casefold().replace(",", "").replace("?", "").replace(".", "").split())
    assert output_words == master_words


def test_basic_hybrid_preserves_gpt_wording_and_aiesa_metadata():
    assert_hybrid(("Привет как дела", "Все хорошо"), "Привет, как дела? Всё хорошо.",
                  ["Привет, как дела?", "Всё хорошо."])


def test_nda_wording_survives_at_same_boundary():
    assert_hybrid(("Это индей", "Это индей да"), "Это NDA? Это NDA, да.",
                  ["Это NDA?", "Это NDA, да."])


def test_negation_adjective_and_closing_phrases_do_not_split():
    assert_hybrid(("Он сказал я не могу разглашать свои источники", "Понятно"),
                  "Он сказал: я не могу разглашать свои источники. Понятно.",
                  ["Он сказал: я не могу разглашать свои источники.", "Понятно."])
    assert_hybrid(("Это был самый невероятный проект", "Согласен"),
                  "Это был самый невероятный проект. Согласен.",
                  ["Это был самый невероятный проект.", "Согласен."])
    assert_hybrid(("Всем хорошего дня", "Пока"), "Всем хорошего дня! Пока.",
                  ["Всем хорошего дня!", "Пока."])


def test_punctuation_trap_cannot_override_lexical_boundary():
    assert_hybrid(("Ну поехали Тестируем бота Поф скажи что-нибудь", "Я говорю"),
                  "Ну, поехали. Тестируем бота. Поф, скажи что-нибудь. Я говорю.",
                  ["Ну, поехали. Тестируем бота. Поф, скажи что-нибудь.", "Я говорю."])


def test_unsafe_alignment_rejected():
    result = align_transcript(turns("совершенно другой текст", "без совпадений"),
                              "alpha beta gamma delta epsilon")
    assert not result.accepted and result.rejection_reason == "global_similarity"


def test_gpt_failure_and_alignment_rejection_select_aiesa_fallback():
    source = turns("исходный один", "исходный два")
    failed = MasterTranscriptionResult("failed", "model", failure_category="timeout")
    assert select_best_transcript(source, failed).turns == tuple(source)
    rejected = MasterTranscriptionResult("success", "model", text="alpha beta gamma delta")
    selection = select_best_transcript(source, rejected)
    assert selection.source == "aiesa_fallback" and selection.turns == tuple(source)


async def test_cleanup_failure_after_hybrid_keeps_uncleaned_hybrid():
    source = turns("Привет как дела", "Все хорошо")
    master = MasterTranscriptionResult("success", "model", text="Привет, как дела? Всё хорошо.")
    selection = select_best_transcript(source, master)
    async def outage(chunk):
        raise ValueError("cleanup_failed")
    delivered, outcome = await cleanup_best_effort(list(selection.turns), outage)
    assert selection.source == "hybrid" and outcome == "failed"
    assert delivered == list(selection.turns)


def test_long_transcript_is_complete_ordered_and_deterministic():
    source = turns(*[f"реплика {i} общий контекст беседы" for i in range(240)])
    master = " ".join(f"Реплика {i}, общий контекст беседы." for i in range(240))
    first = align_transcript(source, master)
    second = align_transcript(source, master)
    assert first == second and first.accepted
    assert len(first.turns) == 240
    assert " ".join(t.text for t in first.turns).count("Реплика") == 240

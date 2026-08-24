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


def test_sentence_completion_stays_before_new_speaker_address():
    assert_hybrid(("ты самый лучший самый невероятный", "коллега простите я перебью"),
                  "Ты самый лучший, самый невероятный. Коллега, простите, я перебью.",
                  ["Ты самый лучший, самый невероятный.", "Коллега, простите, я перебью."])


def test_short_nda_question_survives_between_longer_neighbors():
    assert_hybrid(("я не могу разглашать свои источники", "это индей", "это индей да конечно"),
                  "Я не могу разглашать свои источники. Это NDA? Это NDA, да, конечно.",
                  ["Я не могу разглашать свои источники.", "Это NDA?", "Это NDA, да, конечно."])


def test_short_interruption_and_answer_are_not_absorbed():
    assert_hybrid(("этому человеку можно полностью доверять", "это моя мама", "в том числе"),
                  "Этому человеку можно полностью доверять. Это моя мама? В том числе.",
                  ["Этому человеку можно полностью доверять.", "Это моя мама?", "В том числе."])


def test_long_neighbor_short_turn_trap_preserves_credible_span():
    left = " ".join(f"левый{i}" for i in range(20))
    right = " ".join(f"правый{i}" for i in range(20))
    assert_hybrid((left, "короткий точный ответ", right),
                  f"{left}. Короткий точный ответ. {right}.",
                  [f"{left}.", "Короткий точный ответ.", f"{right}."])


def test_continuous_prepositional_phrase_is_not_split():
    assert_hybrid(("да а будем подключать к этому", "потом проверим результат"),
                  "Да, а будем подключать к этому? Потом проверим результат.",
                  ["Да, а будем подключать к этому?", "Потом проверим результат."])


def test_moderate_asr_mismatch_still_uses_local_anchors():
    assert_hybrid(("обсудим рабочие задачи завтра", "после этого отправим документ"),
                  "Обсудим основные задачи завтра. После этого вышлем документ.",
                  ["Обсудим основные задачи завтра.", "После этого вышлем документ."])


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


def test_legitimate_short_turn_expansion_is_accepted():
    source = ("один два три четыре пять", "прочная соседняя реплика сохраняет границу")
    master = ("один два восстановлено ещё несколько слов три четыре пять. "
              "Прочная соседняя реплика сохраняет границу.")
    result = align_transcript(turns(*source), master)
    assert result.accepted
    assert len(result.turns[0].text.split()) == 9


def test_moderate_long_turn_expansion_is_accepted():
    long_source = " ".join(f"слово{i}" for i in range(30))
    additions = " ".join(f"уточнение{i}" for i in range(12))
    result = align_transcript(turns(long_source, "надёжный конец"),
                              f"{long_source} {additions}. Надёжный конец.")
    assert result.accepted
    assert len(result.turns[0].text.split()) == 42


def test_catastrophic_turn_absorption_is_rejected_with_text_free_diagnostic():
    source = " ".join(f"якорь{i}" for i in range(8))
    absorbed = " ".join(f"лишнее{i}" for i in range(52))
    stable = " ".join(f"стабильный{i}" for i in range(100))
    result = align_transcript(turns(source, stable), f"{source} {absorbed}. {stable}.")
    assert not result.accepted and result.rejection_reason == "pathological_turn"
    diagnostic = result.pathological_turn
    assert diagnostic is not None
    assert (diagnostic.index, diagnostic.source_tokens, diagnostic.hybrid_tokens) == (0, 8, 60)
    assert diagnostic.ratio == 7.5
    assert diagnostic.ratio_limit == 1.6
    assert diagnostic.absolute_slack == 8
    assert diagnostic.token_limit == 20.8


def _production_like_fixture():
    """Synthetic token shapes only; no production transcript wording."""
    sizes = [25, 24, 23, 4, 22, 25, 24, 4, 45, 21, 24, 20, 25, 23, 22, 20, 24, 18]
    assert sum(sizes) == 393
    source_turns = []
    master_turns = []
    cursor = 0
    for turn_index, size in enumerate(sizes):
        source_words = [f"токен{cursor + offset}" for offset in range(size)]
        master_words = list(source_words)
        # Deterministic modest wording differences bring similarity close to the
        # observed production bucket while leaving anchors at every boundary.
        for offset in range(2, size - 2, 9):
            master_words[offset] = f"замена{turn_index}x{offset}"
        additions = 9 if turn_index == 3 else (8 if turn_index == 10 else 0)
        master_words[1:1] = [f"восстановлено{turn_index}x{i}" for i in range(additions)]
        # Production-derived shapes: sentence completion, two short alternating
        # turns, a long-neighbour trap, and a continuous syntactic phrase.  The
        # vocabulary is synthetic and the token totals remain production-like.
        semantic = {
            2: {-4: "самый", -3: "лучший", -2: "самый", -1: "невероятный"},
            3: {0: "коллега", 1: "короткий", 2: "вопрос", 3: "сейчас"},
            7: {0: "точный", 1: "короткий", 2: "ответ", 3: "сейчас"},
            16: {-5: "будем", -4: "подключать", -3: "к", -2: "этому", -1: "позже"},
        }.get(turn_index, {})
        for position, word in semantic.items():
            source_position = position if position >= 0 else size + position
            source_words[source_position] = word
            master_position = source_position + (additions if source_position >= 1 else 0)
            master_words[master_position] = word
        source_turns.append(" ".join(source_words))
        master_turns.append(" ".join(master_words) + ".")
        cursor += size
    assert sum(len(text.split()) for text in master_turns) == 410
    return source_turns, " ".join(master_turns)


def test_production_like_transcript_accepts_restored_words_without_unsafe_boundaries():
    source, master = _production_like_fixture()
    result = align_transcript(turns(*source), master)
    assert result.accepted, result.rejection_reason
    assert 0.85 <= result.similarity <= 0.91
    assert len(result.turns) == 18 and len(result.boundaries) == 17
    assert all(boundary.confidence.value != "low" for boundary in result.boundaries)
    flattened = [token for turn in result.turns for token in turn.text.rstrip(".").split()]
    assert len(flattened) == 410
    assert "самый лучший самый невероятный" in result.turns[2].text
    assert len(result.turns[7].text.rstrip(".").split()) == 4
    assert "подключать к этому" in result.turns[16].text
    # The former max(8, round(source * 3.0)) cap was 12 here, so this
    # legitimate 13-token restoration deterministically reproduced rejection.
    assert len(result.turns[3].text.rstrip(".").split()) == 13

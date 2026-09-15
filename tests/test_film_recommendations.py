from bot.services.film_recommendations import (
    RecommendationCandidate as C, RecommendationConstraints as Constraints,
    build_film_preference_profile, filter_candidates, profiles_for_actor, rank_candidates,
    title_family,
)


def film(reaction=None, actor="vova", **values):
    result = {"title": "Stored", "status": "watched", "genres": ["Комедия"], "media_type": "movie"}
    if reaction is not None: result["reactions"] = {actor: reaction}
    result.update(values); return result


def candidate(id="1", title="Candidate", genres=("Комедия",), **values):
    defaults = dict(external_id=id, media_type="movie", title=title, genres=genres,
                    external_rating=7.5, vote_count=1000, popularity=100)
    defaults.update(values); return C(**defaults)


def test_profile_reaction_semantics_evidence_isolation_and_legacy_rating():
    films = [film("like"), film("dislike"), film("neutral"), film(None, rating=10),
             film("like", actor="sasha", genres=["Драма"]), film("like", status="want")]
    profile = build_film_preference_profile(films, "vova")
    assert (profile.like_count, profile.neutral_count, profile.dislike_count) == (1, 1, 1)
    assert profile.reacted_count == 3 and profile.unknown_watched_count == 2
    assert profile.genres["comedy"].weighted_sum == 0
    assert "drama" not in profile.genres


def test_sparse_profile_is_shrunk_and_unknown_is_cold_start():
    assert build_film_preference_profile([film(None)], "vova").is_cold_start
    profile = build_film_preference_profile([film("like")], "vova")
    assert 0 < profile.genres["comedy"].score < 0.5


def test_filter_excludes_watched_external_fallback_want_and_constraints():
    watched = [film(None, metadata_provider="tmdb", external_id="1"),
               film(None, title="Fallback", localized_title="", year=2020)]
    want = [film(None, status="want", metadata_provider="tmdb", external_id="3")]
    values = [candidate("1"), candidate("2", title="Fallback", year=2020), candidate("3"),
              candidate("4", media_type="tv"), candidate("5", genres=("Ужасы",)),
              candidate("6", year=1990), candidate("7", external_rating=5),
              candidate("8", runtime_minutes=200), candidate("9", runtime_minutes=90, year=2020)]
    constraints = Constraints(media_type="movie", include_genres=frozenset({"Комедия"}),
                              exclude_genres=frozenset({"Ужасы"}), min_year=2000,
                              min_rating=6, max_runtime=120)
    assert [x.external_id for x in filter_candidates(values, watched, want, constraints)] == ["9"]


def test_unknown_watched_is_excluded_and_want_can_be_included():
    stored = film(None, metadata_provider="tmdb", external_id="1")
    assert not filter_candidates([candidate()], [stored], [], Constraints())
    wanted = dict(stored, status="want")
    assert filter_candidates([candidate()], [], [wanted], Constraints(exclude_want=False))


def test_ranking_taste_quality_floor_vote_prior_ties_and_explanations():
    profile = build_film_preference_profile([film("like"), film("like")], "vova")
    low_votes = candidate("0", vote_count=99)
    liked = candidate("1", title="B liked")
    unrelated = candidate("2", title="A unrelated", genres=("Драма",))
    higher_votes = candidate("3", title="Quality", genres=("Драма",), vote_count=10000)
    ranked = rank_candidates([unrelated, low_votes, liked, higher_votes], profile, constraints=Constraints(limit=10))
    assert low_votes not in [x.candidate for x in ranked]
    assert ranked[0].candidate == liked
    assert any("Комедия" in reason for reason in ranked[0].explanation_reasons)
    assert [x.candidate.external_id for x in ranked] == [x.candidate.external_id for x in rank_candidates([higher_votes, liked, unrelated], profile, constraints=Constraints(limit=10))]


def test_dislike_penalizes_and_joint_negative_is_not_cancelled():
    films = [film("like", actor="vova"), film("dislike", actor="sasha")]
    ranked = rank_candidates([candidate()], profiles_for_actor(films, "both"))[0]
    actor_scores = dict(ranked.actor_scores)
    assert actor_scores["vova"] > 0 > actor_scores["sasha"]
    assert ranked.taste_score < 0 and ranked.penalties


def test_joint_cases_are_deterministic_and_cold_actor_does_not_invent_signal():
    cases = [("like", "like"), ("like", "neutral"), ("like", "dislike"), ("dislike", "dislike")]
    totals = []
    for left, right in cases:
        profiles = profiles_for_actor([film(left, actor="vova"), film(right, actor="sasha")], "both")
        totals.append(rank_candidates([candidate()], profiles)[0].total)
    assert totals[0] > totals[1] > totals[2] > totals[3]
    one = profiles_for_actor([film("like", actor="vova")], "both")
    score = rank_candidates([candidate()], one)[0]
    assert dict(score.actor_scores)["sasha"] == 0


def test_joint_positive_alignment_and_neutral_are_distinct_from_dislike():
    both_like = profiles_for_actor(
        [film("like", actor="vova"), film("like", actor="sasha")], "both"
    )
    like_neutral = profiles_for_actor(
        [film("like", actor="vova"), film("neutral", actor="sasha")], "both"
    )
    liked = candidate("liked")
    unrelated = candidate("unrelated", genres=("Драма",))

    aligned = rank_candidates([unrelated, liked], both_like, constraints=Constraints(limit=2))
    neutral = rank_candidates([liked], like_neutral)[0]

    assert aligned[0].candidate == liked
    assert all(value > 0 for _, value in aligned[0].actor_scores)
    assert "one-person negative-fit penalty" not in aligned[0].penalties
    assert aligned[0].taste_score > neutral.taste_score > 0
    assert dict(neutral.actor_scores)["sasha"] == 0
    assert "one-person negative-fit penalty" not in neutral.penalties


def test_joint_explicit_dislike_beats_other_actors_weak_want():
    items = [
        film(None, status="want", added_by="Вова", genres=["Комедия"]),
        film("dislike", actor="sasha", genres=["Комедия"]),
    ]

    score = rank_candidates([candidate()], profiles_for_actor(items, "both"))[0]

    assert dict(score.actor_scores)["vova"] > 0 > dict(score.actor_scores)["sasha"]
    assert score.taste_score < 0
    assert "joint disagreement penalty" in score.penalties
    assert "one-person negative-fit penalty" in score.penalties


def test_joint_shared_want_is_positive_but_weaker_than_shared_explicit_likes():
    wants = [
        film(None, status="want", added_by="Вова", genres=["Комедия"]),
        film(None, status="want", added_by="Саша", genres=["Комедия"]),
    ]
    likes = [film("like", actor="vova"), film("like", actor="sasha")]

    wanted = rank_candidates([candidate()], profiles_for_actor(wants, "both"))[0]
    liked = rank_candidates([candidate()], profiles_for_actor(likes, "both"))[0]

    assert all(value > 0 for _, value in wanted.actor_scores)
    assert 0 < wanted.taste_score < liked.taste_score


def test_joint_different_tastes_reward_a_candidate_that_matches_both():
    profiles = profiles_for_actor(
        [
            film("like", actor="vova", genres=["Комедия"]),
            film("like", actor="sasha", genres=["Драма"]),
        ],
        "both",
    )
    comedy = candidate("comedy", genres=("Комедия",))
    drama = candidate("drama", genres=("Драма",))
    compromise = candidate("compromise", genres=("Комедия", "Драма"))

    ranked = rank_candidates(
        [comedy, drama, compromise], profiles, constraints=Constraints(limit=3)
    )

    assert ranked[0].candidate == compromise
    assert ranked[0].taste_score > next(
        score.taste_score for score in ranked if score.candidate == comedy
    )
    assert ranked[0].taste_score > next(
        score.taste_score for score in ranked if score.candidate == drama
    )


def test_joint_rich_and_cold_profiles_use_only_real_evidence():
    profiles = profiles_for_actor(
        [film("like", actor="vova"), film("like", actor="vova")], "both"
    )
    matching = candidate("matching")
    unrelated = candidate("unrelated", genres=("Драма",))

    ranked = rank_candidates(
        [unrelated, matching], profiles, constraints=Constraints(limit=2)
    )

    assert ranked[0].candidate == matching
    assert dict(ranked[0].actor_scores)["sasha"] == 0
    assert "joint disagreement penalty" not in ranked[0].penalties


def test_mixed_explicit_genres_keep_negative_evidence_visible_and_deterministic():
    profile = profiles_for_actor(
        [
            film("like", actor="vova", genres=["Комедия"]),
            film("dislike", actor="vova", genres=["Ужасы"]),
        ],
        "vova",
    )
    mixed = candidate("mixed", genres=("Комедия", "Ужасы"))

    first = rank_candidates([mixed], profile)[0]
    second = rank_candidates([mixed], profile)[0]

    assert first.taste_score == second.taste_score == 0
    assert any("отрицательный сигнал" in reason for reason in first.explanation_reasons)


def test_joint_ranking_is_input_order_invariant_and_actor_symmetric():
    histories = [
        film("like", actor="vova", genres=["Комедия"]),
        film("dislike", actor="sasha", genres=["Ужасы"]),
    ]
    swapped = [
        film("like", actor="sasha", genres=["Комедия"]),
        film("dislike", actor="vova", genres=["Ужасы"]),
    ]
    values = [
        candidate("comedy", title="Comedy", genres=("Комедия",)),
        candidate("horror", title="Horror", genres=("Ужасы",)),
        candidate("mixed", title="Mixed", genres=("Комедия", "Ужасы")),
    ]

    forward = rank_candidates(
        values, profiles_for_actor(histories, "both"), constraints=Constraints(limit=3)
    )
    reverse = rank_candidates(
        reversed(values), profiles_for_actor(histories, "both"), constraints=Constraints(limit=3)
    )
    symmetric = rank_candidates(
        values, profiles_for_actor(swapped, "both"), constraints=Constraints(limit=3)
    )

    assert [score.candidate.external_id for score in forward] == [
        score.candidate.external_id for score in reverse
    ]
    assert [(score.total, score.taste_score) for score in forward] == [
        (score.total, score.taste_score) for score in symmetric
    ]


def test_diversity_promotes_close_different_primary_genre():
    profile = build_film_preference_profile([], "vova")
    values = [candidate(str(i), genres=("Комедия",), popularity=100-i) for i in range(5)]
    values.append(candidate("x", genres=("Драма",), popularity=90))
    ranked = rank_candidates(values, profile, constraints=Constraints(limit=3))
    assert any(x.candidate.external_id == "x" for x in ranked)


def test_want_is_weak_owned_interest_and_never_legacy_taste():
    items = [film(None, status="want", added_by="Вова", genres=["Комедия"]),
             film(None, status="want", added_by="Саша", genres=["Драма"]),
             film(None, status="want", added_by="unknown", genres=["Ужасы"]),
             film(None, rating=10)]
    vova = build_film_preference_profile(items, "vova")
    sasha = build_film_preference_profile(items, "sasha")
    assert vova.want_interest_count == 1 and set(vova.want_genres) == {"comedy"}
    assert sasha.want_interest_count == 1 and set(sasha.want_genres) == {"drama"}
    assert vova.reacted_count == 0 and vova.unknown_watched_count == 1


def test_explicit_like_beats_want_and_dislike_dominates_it():
    wanted = film(None, status="want", added_by="Вова", genres=["Комедия"])
    liked = build_film_preference_profile([film("like")], "vova")
    weak = build_film_preference_profile([wanted], "vova")
    assert rank_candidates([candidate()], liked)[0].taste_score > rank_candidates([candidate()], weak)[0].taste_score > 0
    conflict = build_film_preference_profile([film("dislike", genres=["Ужасы"]), wanted], "vova")
    scored = rank_candidates([candidate(genres=("Комедия", "Ужасы"))], conflict)[0]
    assert scored.taste_score < 0


def test_joint_want_signals_are_independent_and_explicit_dislike_wins():
    items = [film(None, status="want", added_by="Вова", genres=["Комедия"]),
             film(None, status="want", added_by="Саша", genres=["Драма"]),
             film("dislike", actor="sasha", genres=["Комедия"])]
    score = rank_candidates([candidate(genres=("Комедия",))], profiles_for_actor(items, "both"))[0]
    assert dict(score.actor_scores)["vova"] > 0 > dict(score.actor_scores)["sasha"]
    assert score.taste_score < 0


def test_current_want_mode_can_disable_circular_interest_stably():
    items = [film(None, status="want", added_by="Вова", genres=["Комедия"]), film("like", genres=["Драма"])]
    profile = profiles_for_actor(items, "vova", include_want=False)
    values = [candidate("c", genres=("Комедия",)), candidate("d", genres=("Драма",))]
    first = rank_candidates(values, profile, constraints=Constraints(limit=2, exclude_want=False))
    second = rank_candidates(reversed(values), profile, constraints=Constraints(limit=2, exclude_want=False))
    assert profile.want_interest_count == 0
    assert [x.candidate.external_id for x in first] == ["d", "c"]
    assert [x.candidate.external_id for x in first] == [x.candidate.external_id for x in second]


def test_want_without_genres_fabricates_no_genre_evidence_but_enriched_genres_participate():
    poor = film(None, status="want", added_by="Вова", genres=[], media_type="")
    enriched = dict(poor, metadata_provider="tmdb", external_id="42", media_type="movie", genres=["Комедия"])

    poor_profile = build_film_preference_profile([poor], "vova")
    enriched_profile = build_film_preference_profile([enriched], "vova")

    assert poor_profile.want_interest_count == 0
    assert poor_profile.want_genres == {}
    assert rank_candidates([candidate()], poor_profile)[0].taste_score == 0
    assert enriched_profile.want_interest_count == 1
    assert set(enriched_profile.want_genres) == {"comedy"}
    assert rank_candidates([candidate()], enriched_profile)[0].taste_score > 0


def test_franchise_family_diversity_and_fallback():
    profile = build_film_preference_profile([], "vova")
    values = [candidate("s1", title="Spider-Man", popularity=100),
              candidate("s2", title="Spider-Man 2", popularity=99),
              candidate("s3", title="Spider-Man 3", popularity=98),
              candidate("other", title="Arrival", genres=("Драма",), popularity=90)]
    ranked = rank_candidates(values, profile, constraints=Constraints(limit=3))
    assert sum(x.candidate.external_id.startswith("s") for x in ranked) < 3
    assert title_family("Дюна: Часть вторая") == "дюна"
    assert title_family("Love Actually") == ""
    fallback = rank_candidates(values[:3], profile, constraints=Constraints(limit=3))
    assert len(fallback) == 3

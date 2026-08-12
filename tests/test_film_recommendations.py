from bot.services.film_recommendations import (
    RecommendationCandidate as C, RecommendationConstraints as Constraints,
    build_film_preference_profile, filter_candidates, profiles_for_actor, rank_candidates,
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
    assert profile.genres["комедия"].weighted_sum == 0
    assert "драма" not in profile.genres


def test_sparse_profile_is_shrunk_and_unknown_is_cold_start():
    assert build_film_preference_profile([film(None)], "vova").is_cold_start
    profile = build_film_preference_profile([film("like")], "vova")
    assert 0 < profile.genres["комедия"].score < 0.5


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


def test_diversity_promotes_close_different_primary_genre():
    profile = build_film_preference_profile([], "vova")
    values = [candidate(str(i), genres=("Комедия",), popularity=100-i) for i in range(5)]
    values.append(candidate("x", genres=("Драма",), popularity=90))
    ranked = rank_candidates(values, profile, constraints=Constraints(limit=3))
    assert any(x.candidate.external_id == "x" for x in ranked)

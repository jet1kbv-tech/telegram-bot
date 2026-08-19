# Natural-language film addition

Natural-language movie and TV additions are catalogue-first. Polza performs one
intent parse and returns only `add_movie_or_tv` plus the user's original search
query; it does not choose or correct a canonical title and does not supply movie
metadata.

The NL handler then hands the query to the native Films metadata-search flow.
The configured metadata provider supplies the canonical candidate title and its
external identity, type, year, genres, description, and rating. Candidate
selection, duplicate handling, preview, confirmation, and saving are therefore
identical to a film started from the Films menu.

If catalogue search is unavailable or finds nothing, saving the raw query is
available only after the user explicitly chooses **Добавить только по
названию**. The NL parsing progress message is removed before the Films search
UI is shown.

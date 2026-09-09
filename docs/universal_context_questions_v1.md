# FEAT-07: Universal contextual questions v1

## Architecture and safety audit

The existing Context Engine remains the sole factual boundary. It builds frozen `EventContext`,
`DocumentContext`, and `TripContext` values from one storage snapshot without I/O. Manual Calendar
records are visible only in the requesting actor's bucket; active Afisha records are shared. Calendar
Afisha projections are deliberately skipped, while attachment parents are canonicalized by the existing
visibility helper. Opaque context IDs are hashes of canonical identities and never provider-supplied IDs.

Before FEAT-07, `query_context` supported trip departure, arrival, exact opposite-route return, document
count, and overview. `query_calendar` and `query_afisha` independently supported list/count/next. The
structured Polza parser classified and extracted bounded arguments; the handler derived the actor from the
allow-listed profile, loaded storage, called a deterministic resolver, and formatted Telegram output.
Trip cards, actor-bound attachment retrieval, weather context, and confirmation-first mutation proposals
were already separate flows and remain unchanged.

FEAT-07 extends the existing `query_context` schema and resolver rather than introducing another Q&A
layer. The pipeline is: one NL classification, bounded semantic arguments, one actor-scoped Context Engine
bundle, deterministic filtering/selection, and local formatting. Resolution never calls mutation,
notification, reminder, weather, or persistence services.

## Supported behavior

The added query families are period schedules, next matching event, event date, event time, event place,
and event-linked document labels. Existing trip departure, arrival, return, overview, and documents remain
supported. Examples include “Что у меня завтра?”, “Какие у нас планы на выходные?”, “Когда следующий
концерт?”, “Во сколько концерт?”, “Где проходит концерт?”, “Есть билеты на концерт?”, “Во сколько поезд
в Воронеж?”, and “Когда мы возвращаемся из Воронежа?”.

Temporal parsing reuses `nl_dates`: today/tomorrow/day-after-tomorrow are local calendar dates; a weekday
means its next occurrence (today's weekday means seven days later); weekend means Saturday through Sunday;
next weekend means the following Saturday through Sunday; next week means the next ISO Monday through
Sunday; explicit ISO, `DD.MM[.YYYY]`, and Russian named dates are supported. All boundaries use
`BOT_TIMEZONE`. Unsupported expressions fail closed and ask for a clearer formulation.

Schedules are chronological and contain each canonical Afisha event once. The provider's optional
`person` value is descriptive only and is intentionally ignored for authorization, so it cannot expose the
other user's manual Calendar. Shared Afisha remains visible. Field queries with multiple matches return a
numbered clarification; explicitly asking for the next event chooses the first future chronological match.
Not-found, missing field, empty period, and no-document outcomes have distinct responses. Document answers
contain semantic labels only, never Telegram file IDs, provider IDs, or attachment IDs.

Single-turn questions with an explicit target are supported. Follow-ups such as “а обратно?”, “а где?”,
“а билеты?”, and “а на следующий день?” are classified as unsupported conversation and remain deferred to
FEAT-09. Wishlist, films, places, purchases, birthdays, recommendations, semantic/vector/web search,
general knowledge, and conversational memory are outside this feature.

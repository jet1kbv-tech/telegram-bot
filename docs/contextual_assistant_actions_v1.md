# Contextual Assistant Actions v1

## Entry points and architecture

Afisha and Calendar detail keyboards append a compact assistant section. The
callback path is `ctx:event:<action>:<a|c>:<canonical-id>:<page>`: Telegram actor
resolution → fresh storage snapshot → canonical Context Engine event → local
domain service → deterministic edited-message response. Callback payloads carry
no document, forecast, route, or provider data.

## Visibility

* **Weather** is available for every visible canonical event because existing
  Weather Context permits the Moscow fallback; an explicit event location or
  selected trip destination takes precedence.
* **Documents** appears only when the actor can see at least one canonical event
  attachment. Legacy Tickets are never consulted.
* **Trip** appears only when one segment can be selected conservatively. Exact
  arrival on the event date wins, then the unique closest arrival within three
  days, then exact departure, then a sole linked candidate. Ambiguity hides the
  action. Independent city routes are never flattened.
* **What is known** is available for every visible canonical event and contains
  only stored date/time, location, selected trip, and document count. It never
  includes weather.

## Reused services and behavior

The feature uses `context_engine` canonical event/document/trip projection, the
same trip selector as notification enrichment, `weather_context.format_forecast`,
the configured `WeatherProvider`, and the existing event attachment list/detail/
send flow. Weather uses selected-trip arrival destination/date, otherwise event
location/date, otherwise Moscow/date. Horizon and provider failures have bounded
Russian messages and do not mutate event data.

## Privacy, stale callbacks, and navigation

Actor scope is derived only from the Telegram user profile. Manual Calendar
events and their documents are owner-private; active Afisha is shared according
to existing visibility. Every callback rebuilds context, so deleted, inactive,
moved, or newly invisible events produce a controlled stale response. Documents
are requeried, including the empty-between-render-and-click race. Results edit
the current detail message and include **Back to event** and home navigation;
file delivery remains in the established attachment flow.

## Provider budget and limitations

Weather may make the normal WeatherProvider lookup. All other actions are local;
the Polza budget is zero. v1 adds no notification, web search, Places, mutation,
trip inference, or autonomous action. City extraction remains intentionally
limited by Context Engine semantics. Ambiguous trips are hidden rather than
guessed, and the existing attachment screen remains the bounded chooser for
multiple documents.

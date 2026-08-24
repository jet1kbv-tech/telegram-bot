# AI Audio Transcription — architecture and Hybrid Transcription v2

## Architecture audit and old production pipeline

Before v2, the handler downloaded Telegram media once into a mode-0700 temporary directory,
streamed that file to Aiesa multipart upload, deleted it, and persisted only the Aiesa job ID and
routing/lifecycle metadata in `ai_jobs`. The scheduler polled Aiesa, retrieved its result, ran
`normalize_segments()`, applied best-effort conservative Polza chat cleanup, generated a DOCX,
delivered it to Telegram, and deleted the DOCX directory. Thus the exact old flow was:

`Telegram media -> Aiesa transcription/diarization -> normalized turns -> Polza cleanup -> DOCX -> Telegram -> cleanup`.

The existing persisted states, retry/backoff (eight bounded attempts, maximum 15 minutes), scheduler,
DOCX format/delivery, and temporary cleanup remain unchanged. Aiesa continues to use its multipart
upload. Polza receives a JSON request whose `file` field is a request-local base64 Data URL.

## Hybrid Transcription v2

After the one Telegram download, Aiesa job creation and Polza `POST /api/v1/audio/transcriptions`
run concurrently against the same on-disk file. Polza uses `openai/gpt-4o-mini-transcribe`, Russian,
and JSON response format without diarization. The request is authenticated with the existing bearer
token and has `Content-Type: application/json`; its body contains `model`, `language`,
`response_format`, and `file: data:<audio MIME>;base64,<audio>`. A safe supplied audio MIME is used,
with an extension-derived type or `audio/ogg` fallback. Its typed result contains only outcome, model,
text, and a bounded failure category. The master result lives only in process memory until Aiesa completes;
it is deliberately not persisted. A restart therefore safely degrades that job to Aiesa rather than
persisting private text. Once both results are available, the flow is:

```
audio -> Aiesa (speakers, order, timestamps) --+
audio -> Polza master ASR (wording) -----------+-> deterministic alignment
    -> conservative existing cleanup -> existing DOCX -> Telegram -> cleanup
```

Source-of-truth rules are explicit: Aiesa owns speaker labels, order, turn count, and timestamps.
An accepted master alignment owns wording and punctuation. Otherwise normalized Aiesa wording is
used. Cleanup can change only wording accepted by its existing preservation validator; it cannot
change speaker metadata.

## Deterministic alignment and safety

Unicode word tokens are case-folded and punctuation-free only for comparison. Actual master spans
are never rewritten. `SequenceMatcher(autojunk=False)` supplies global similarity and matching-token
anchors. Each cumulative Aiesa turn boundary is projected between its nearest left/right anchors
(or from one anchor/proportion when necessary).

The search radius is dynamic: a base derived from one third of the smaller neighboring turn plus a
quality allowance (1 token at similarity >=0.85, 2 at >=0.65, otherwise 3), clamped to a minimum of
1 and maximum of 6 tokens. An independent absolute maximum shift of 8 tokens is enforced. Candidate
distance costs four points per token; punctuation contributes at most three (`.!?` 3, `;:` 2, comma
1, whitespace/dash 0). Consequently punctuation breaks only a same-distance/local tie and can never
move a boundary one unsupported token. This specifically protects clauses such as negations,
adjective/noun pairs, closing phrases, and the `Поф, скажи что-нибудь` punctuation trap.

Boundary confidence is categorical. HIGH requires a <=1-token move and lexical anchors on both
sides; MEDIUM permits <=2 with an anchor and global similarity >=0.65; all else is LOW. Acceptance
requires global similarity >=0.45 (low enough for systematic lexical corrections such as
`индей`/`NDA`), zero LOW boundaries, strictly increasing non-empty boundaries, unchanged Aiesa
metadata/order/turn count, exact ordered coverage of every master token once, and no turn exceeding
the greater of eight tokens or three times its Aiesa token count. These local and structural guards
are intentionally stricter than a global ratio alone.

## Fallback and cleanup

* Aiesa + successful safe alignment: hybrid wording.
* GPT failure, missing in-memory result after restart, or rejected alignment: normalized Aiesa.
* Aiesa failure: unchanged existing terminal/retry behavior; speakers are never invented.
* Cleanup is applied after source selection, chunked only between turns. A failed chunk uses its
  uncleaned hybrid or Aiesa input, so usable transcription is never discarded.

The cleanup guard remains fail-closed: identities/timestamps/count/order must match; standalone word
insertion/deletion fails; token count must retain 75%, token similarity 0.60, and compact-character
similarity 0.82. DOCX contains only the best safe transcript and exposes no provider or alignment data.

## Long recordings, privacy, and configuration

The configured input cap remains 50 MB. On the 1-vCPU/~2-GB/no-swap host, the downloaded file is one
disk copy. Aiesa streams multipart data, while the Polza request temporarily holds the required base64
encoding only in memory and neither persists nor logs it; provider/network buffers add bounded overhead.
Alignment keeps token/span objects
and two text representations in memory and is local diff work, not ASR. No ffmpeg/transcoding or new
service is required. `SequenceMatcher` can have quadratic worst cases; acceptance is deterministic and
the one-hour synthetic regression guards representative behavior, while the 50-MB cap bounds input.

Logs contain structural outcome, safe failure category, similarity bucket, counts, selected fallback,
cleanup totals, and lifecycle only—never audio, text/excerpts, prompts/payloads, credentials, Telegram
IDs, or private filenames. Media/DOCX cleanup is unchanged; no master-response/debug temp artifact is
created. Persistence remains schema-compatible and contains no transcript/provider payload.

Required Aiesa and Polza credentials are unchanged. `POLZA_TRANSCRIPTION_MODEL` optionally overrides
the default `openai/gpt-4o-mini-transcribe`; `POLZA_AI_API_KEY` is reused. Cleanup still uses
`AI_TRANSCRIPTION_CLEANUP_MODEL` (falling back to `POLZA_AI_MODEL`). No migration or deployment step is
required beyond setting the optional model value and restarting normally.

## Production smoke procedure (do not deploy as part of this change)

1. Short two-speaker recording: verify Aiesa labels/timestamps and natural master wording.
2. NDA/English abbreviation; verify `NDA` survives.
3. Professional terminology/anglicisms.
4. Negation, especially `не могу разглашать`.
5. Fillers and repetitions remain present after cleanup.
6. Speaker interruption.
7. Very short speaker turn.
8. Long sentence near a speaker change, including the punctuation trap.
9. Disable/fail master request and verify Aiesa fallback.
10. Fail cleanup and verify uncleaned selected transcript delivery.
11. A realistic 10–20-minute recording.
12. An approximately one-hour recording within the configured cap; observe CPU/RAM and latency.
13. After success and terminal failure, inspect temp storage for artifacts.
14. Inspect logs for structural fields only and absence of private data.
15. Verify unchanged DOCX name, formatting, caption, and Telegram file delivery.

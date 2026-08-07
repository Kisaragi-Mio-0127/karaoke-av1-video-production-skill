# MMS Workflow Contract

Use this reference for the three route decisions shared by both local Skills.
It is normative for private local MMS evidence and release hand-off. Run every
command from the StrangeUtaGame project root with `uv run --no-sync`.

## Model and cache boundary

Use project-owned model paths:

- MMS forced-alignment checkpoint: `models/mms/model.pt`.
- Independent ASR/Whisper weights: `models/whisper/<model>.pt`, or the
  `models/whisper` directory through `--model-cache`.

Use `--mms-model-path` or `--model-path` for an explicit local file. Do not
use a `.cache` file as an implicit model substitute and do not allow model
download fallback.

Use `.cache` only for derived runtime data, such as `.cache/msst-vocals`,
`.cache/asr-recognition`, and other task-owned evidence caches. A legacy
`.cache/torch` or `.cache/whisper` checkpoint is not the canonical model
source. Reports must record the resolved model path, model identity, and
recognition cache key/path without exposing unnecessary absolute paths.

## MMS identity and gate boundary

The dedicated Japanese and zh/en MMS entry points do not perform hash checks.
Any `*_sha256` field is report-only metadata: never use it in input checks,
quality gates, exception handling, or process exit status.

Resolve and validate gate inputs using:

- absolute paths for selected project, source, model, audio, and artifact inputs;
- the expected schema and required fields;
- song identity and language;
- token/index correspondence;
- timeline order, bounds, and `sentence_end_ts`; and
- for a full zh/en release, the independent `stem` and `mix` ASR semantics,
  including `support`, `veto`, and `unresolved` decisions.

Use sanitized forms only when writing reports; path resolution itself remains
absolute. Existing normal non-MMS ASS/encoding checks are unchanged and
outside this MMS contract.

## Artifact contract

The normal routes do not create MMS artifacts. A dedicated full MMS run uses a
new private output root outside deliverables and follows:

```text
audit -> recognition gate when required -> build -> companion SUG -> render
```

`audit-only` is supported by the zh/en wrapper. It writes audit evidence and a
workflow report, then stops; it does not run recognition, create `build/` or
`render/`, create a companion SUG, or authorize release. The Japanese wrapper
has no `--audit-only` option; use `audit_karaoke_mms_alignment.py` for a
Japanese evidence-only audit.

## Full build and release decision

Treat build structure and release quality as separate gates. A structurally
valid full build must write `build/timing_overrides.json` and then create
`build/<stem>.mms-editable.sug` before deciding whether a release may render.
The companion is a derived editable review artifact; create it without
overwriting the canonical reviewed SUG and preserve the original SUG bytes and
identity as the rollback/release fallback.

After the companion exists, evaluate audit quality, build quality, and (for
zh/en) both ASR lanes. If any evidence is `unresolved`, a quality gate is
false, or an ASR lane is vetoed, keep the companion and sidecar for manual
timing adjustment, do not create `render/` or call the renderer, write
`status: review-required`, and exit non-zero. This is a review hand-off, not a
reason to discard the valid companion.

When all release gates pass, inspect `visual_release_overrides_ms` separately:

- If it is non-empty, render the companion and pass
  `build/timing_overrides.json` as the visual-release sidecar. Character timing
  evidence remains in the companion and is not folded into the canonical SUG.
- If it is absent or empty, do not fail the build for that reason. Render the
  companion without a timing sidecar; retain the companion's canonical
  `sentence_end_ts` as the release boundary. Retain the timing artifact as
  review evidence and record the companion as the release SUG with
  `visual_release_applied_to_render: false`.

Always record companion, sidecar, release-SUG, and gate identities in the
workflow report. A structural build failure before companion creation remains a
hard failure; the no-visual-release case is not.

## Normal routes: no MMS

Japanese:

```powershell
uv run --no-sync python scripts/run_karaoke_japanese_workflow.py `
  --sug <project.sug> --audio <post-mix-audio> `
  --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style spectrum
```

Chinese or English:

```powershell
uv run --no-sync python scripts/run_karaoke_zh_en_workflow.py `
  --language <zh-or-en> --sug <project.sug> --audio <post-mix-audio> `
  --output-dir <new-output-dir> `
  --title <title> --artist <artist> `
  --album-title <album-title> --album-artist <album-artist> `
  --visual-style spectrum
```

These are real one-click entry points. They accept no MMS mode and must not
invoke MMS. They generate the current composition in the new output directory;
`vinyl` also generates a fresh record asset there, while `spectrum` generates
and reports no vinyl. Explicit `--composition` and `--vinyl` values are
advanced compatibility overrides.

## Japanese MMS route

Use the dedicated entry only for a Japanese manifest track. The manifest
selects the canonical SUG and mix; `--source` and `--vocals-root` are optional
local overrides. Spectrum avoids the vinyl-only argument. The Japanese-only
`--quality-policy` accepts `strict` or `auto-fallback` and defaults to
`strict`:

```powershell
# strict (default)
uv run --no-sync python scripts/run_karaoke_japanese_mms_workflow.py `
  --manifest <manifest> --song-id <song-id> `
  --output-dir <new-private-output-dir> `
  --mms-model-path models/mms/model.pt `
  --quality-policy strict `
  --visual-style spectrum
```

For an automatic fallback run, replace the policy line in the command above
with:

```powershell
  --quality-policy auto-fallback `
```

`strict` keeps the normal review hand-off: after a structurally valid
companion is created, a failed quality gate or unresolved evidence leaves the
companion for manual timing review, skips render, and exits non-zero.
`auto-fallback` does not require that manual review to complete the automated
run. It applies high-confidence MMS timing and retains the canonical timing
for low-confidence or unresolved units. The original quality-gate and
`unresolved` evidence remains in the report, with the fallback decision
recorded explicitly (including `release_decision.quality_gate_overridden` when
the quality gate is overridden); it is not rewritten as a quality pass.
Structural build, subtitle, and media hard gates remain blocking in both
policies.

When auto-fallback renders successfully, the workflow report uses
`status: rendered-with-fallback` and the process exits 0. That status means
only that the automated workflow completed; it does not certify the quality
gate or claim that a human review occurred. The derived
`build/<stem>.mms-editable.sug` remains separate from the canonical SUG and may
be manually re-timed later without overwriting the canonical source.

Ruby and MMS are complementary, not interchangeable: preserve and review ruby
in the canonical SUG, ignore pure-katakana ruby in the read-only canonical
view, pass the remaining reviewed reading/alignment units to MMS first, and use
generic reading fallback only for uncovered visible text. Do not let timing
fallback rewrite ruby. Numeric and mixed text use the same generic mapping;
release-specific corrections belong in external review data, not shared code.

For evidence-only Japanese alignment, use the actual audit CLI instead of an
unsupported wrapper flag:

```powershell
uv run --no-sync python scripts/audit_karaoke_mms_alignment.py `
  --manifest <manifest> --song-id <song-id> `
  --source <frozen-lyrics.json> --vocals-root .cache/msst-vocals `
  --model-path models/mms/model.pt `
  --output <private-output>/audit/mms_alignment_audit.json
```

The Japanese full route audits the original mix and matching MSST vocals,
builds the companion before the release decision, and renders only after the
audit/build/media quality gates pass. It never mutates the manifest, canonical
SUG, frozen lyrics, MSST vocals, or accepted video; without visual-release
overrides, a passing run still renders the companion without a timing sidecar
and uses its preserved canonical `sentence_end_ts` release.

## Chinese/English MMS route

Use the dedicated local wrapper for `zh` or `en`. `--source` is required and
must point to the frozen local lyric source. Audit-only is review-only:

```powershell
uv run --no-sync python scripts/run_karaoke_zh_en_mms_workflow.py `
  --manifest <manifest> --song-id <song-id> --language <zh-or-en> `
  --source <frozen-lyrics.json> --output-dir <new-private-output-dir> `
  --mms-model-path models/mms/model.pt --audit-only
```

Full mode requires exactly two structurally valid independent recognition
reports: one `stem` and one `mix`. Both must have `support` for release render;
a veto or unresolved lane still permits structurally valid build/companion
creation, then blocks render and exits non-zero:

```powershell
uv run --no-sync python scripts/run_karaoke_zh_en_mms_workflow.py `
  --manifest <manifest> --song-id <song-id> --language <zh-or-en> `
  --source <frozen-lyrics.json> --output-dir <new-private-output-dir> `
  --mms-model-path models/mms/model.pt `
  --recognition-audit <stem-recognition.json> `
  --recognition-audit <mix-recognition.json> `
  --visual-style spectrum
```

For full vinyl mode, replace `spectrum` with `vinyl`; the current run generates
the vinyl asset. Do not pass `--vinyl` to spectrum.

Create the two recognition reports with the independent-ASR CLI, not with
MMS or stable-ts prompts:

```powershell
uv run --no-sync python scripts/audit_karaoke_asr_recognition.py `
  --manifest <manifest> --source <frozen-lyrics.json> --song-id <song-id> `
  --language <zh-or-en> --audio <stem-audio> --audio-kind stem `
  --model-cache models/whisper --cache-dir .cache/asr-recognition `
  --output <stem-recognition.json>

uv run --no-sync python scripts/audit_karaoke_asr_recognition.py `
  --manifest <manifest> --source <frozen-lyrics.json> --song-id <song-id> `
  --language <zh-or-en> --audio <mix-audio> --audio-kind mix `
  --model-cache models/whisper --cache-dir .cache/asr-recognition `
  --output <mix-recognition.json>
```

## Language contract

- `ja`: MMS receives the reviewed Japanese timing source and its supplied
  reading/alignment units. Pure katakana receives no separate ruby. Keep ruby
  review separate from timing and reject cross-singer ruby spans.
- `zh`: supply whole-sentence context plus tone-less pinyin syllables for
  forced alignment only. Preserve the frozen Chinese display text.
- `en`: supply orthographic whole words for forced alignment only. Keep one
  editable timing unit per word; renderer-only letter sweep interpolation must
  never be persisted to the SUG.
- `zh` and `en` render zero ruby, zero pinyin, and zero phonetic overlays.
- MMS and stable-ts are supplied-token forced alignment, not independent ASR
  or phoneme recognition. Independent ASR must transcribe without lyric
  prompting and remain separate evidence.

## Batch hand-off

The direct AV1 batch entry never invokes MMS. Validate any pre-existing sidecar
before letting batch consume it:

```powershell
uv run --no-sync python scripts/render_karaoke_direct_av1_420_album.py `
  --manifest <manifest> --song <song-id> --single-track --profile wide `
  --visual-style <vinyl-or-spectrum> --jobs 1
```

Use `--visual-style both` only when two isolated style outputs are intended.
Apply the ordinary AV1/media gates to each output. A failed or unresolved MMS
run remains private review evidence and must not replace a release artifact.

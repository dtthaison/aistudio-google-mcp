# aistudio

An unofficial command-line tool for your own [Google AI Studio](https://aistudio.google.com)
and [Google Flow](https://flow.google.com) account. It signs in with your normal Google web
session, so **no Gemini API key is needed**.

> [!WARNING]
> This project is not made or endorsed by Google. It drives undocumented internal endpoints and
> pages of AI Studio and Flow, so it can break whenever Google ships a change. Automating a
> personal Google session may violate Google's Terms of Service. Use a dedicated Google account,
> and use it at your own risk.

## What you can do

| Area | Commands | Cost |
|---|---|---|
| Models and account | `models list/get/star`, `count-tokens`, `prompt list`, `voices list`, `status` | free |
| Build apps | `app list/get/recent/export`, `gallery list/get` | free |
| Build apps | `app create`, `app prompt` | your Build quota |
| Media | `generate image/speech/music`, `transcribe` | your AI Studio quota |
| Google Flow | `flow projects/media/download/credits`, `flow scene ...`, `flow frame save` | free |
| Google Flow | `flow video/image/extend/edit`, `flow character ...` | Flow credits (only with `--max-credits`) |
| Several accounts | `profile create/list/switch/delete`, `-p NAME` on any command | free |

Run `aistudio --help`, or `aistudio <command> --help`, for every option.

## Examples

Each result below is what that prompt produced, unedited apart from resizing for the repo.

### Image

```bash
aistudio generate image "A small red lighthouse on a rocky shore at sunset, with the word 'default' painted on its side" -o lighthouse.jpg
```

<img src="docs/samples/image-lighthouse.jpg" alt="A red lighthouse labelled DEFAULT on a rocky shore at sunset" width="640">

Default model `gemini-3.1-flash-lite-image`, about $0.034 per image from your quota.

### Speech

```bash
aistudio generate speech "Hello, this is the default account testing speech." -o hello.wav
```

▶️ [Listen: `docs/samples/speech-hello.m4a`](docs/samples/speech-hello.m4a) (4 s, voice Fola,
model `gemini-3.8-flash-tts`; converted from the .wav to .m4a to keep the repo small)

Pick a voice with `--voice` (see `aistudio voices list`) and a manner with `--style "warm, slow"`.
Expression tags such as `<laugh>` go inside the text.

### Build app

```bash
aistudio app create "A page that says hello in big friendly letters (CLI test on second)"
```

<img src="docs/samples/app-hello-big.jpg" alt="AI Studio Build: the builder's summary on the left, the finished Big Friendly Hello app on the right" width="800">

The build ran for about 5½ minutes in a minimized Chrome window and produced 18 files. Then
`aistudio app export <id> ./hello-big` downloaded them, each checked against its SHA-256. When
Build makes two versions at once ("Option A / Option B"), `app create` waits for both and keeps
the first one shown, as if you had pressed "Skip comparison". It reports the id of the other.

### Video (Google Flow)

```bash
aistudio flow video "a red apple rolling on a table" -m veo-3.1-lite --duration 8 --max-credits 10 -o apple.mp4
```

[<img src="docs/samples/video-apple-poster.jpg" alt="A red apple rolling across a sunlit wooden table" width="640">](docs/samples/video-apple.mp4)

▶️ [Watch: `docs/samples/video-apple.mp4`](docs/samples/video-apple.mp4) (8 s, Veo 3.1 Lite,
10 Flow credits)

Without `--max-credits` the same command only shows Flow's price quote and spends nothing.

## Requirements

- **Python 3.10+** and [uv](https://docs.astral.sh/uv/). Developed and used on macOS; CI runs
  the unit tests on Linux.
- **Google Chrome**. Commands that drive a page (generation, Build, Flow) run it in a minimized
  window, because Google refuses headless browsers for generation.
- A Google account that can use AI Studio. Flow needs a Google AI plan with Flow credits.

## Install

The package is not on PyPI. Install it from GitHub:

```bash
uv tool install "aistudio-py[browser] @ git+https://github.com/dtthaison/aistudio-google-mcp"
```

Or from a clone, tracking your working copy:

```bash
git clone https://github.com/dtthaison/aistudio-google-mcp && cd aistudio-google-mcp
uv tool install --editable '.[browser]'      # puts `aistudio` on PATH (~/.local/bin)
```

## Quick start

```bash
aistudio login                      # a Chrome window opens; sign in to Google once
aistudio auth check --test          # check the saved session with one real call
aistudio models list --category images
aistudio app list                   # your Build apps
aistudio generate image "a red apple on a white table" -o apple.jpg
```

Every command takes `--json` for machine-readable output.

<details>
<summary>More commands</summary>

```bash
aistudio models get lyria-3.5
aistudio models star lyria-3.5                 # stars sync with the AI Studio picker
aistudio prompt list --type chat
aistudio count-tokens "hello world" -m gemini-flash-latest
aistudio app get <id-prefix>                   # one app and its builder runs
aistudio app prompt <id-prefix> "make the start button green"
aistudio app export <id-prefix> ./my-app       # download an app's code (verified)
aistudio gallery list --tag google-maps
aistudio status                                # AI Studio / Gemini API incidents
aistudio voices list
aistudio generate speech "Hello! <laugh> Nice to meet you." --voice Tova --style "warm, slow" -o hello.wav
aistudio generate music "calm solo piano, slow tempo" -o piano.mp3
aistudio generate image "make the red squares blue" --image board.png -o blue.jpg   # edit an image
aistudio transcribe talk.m4a -o talk.txt       # speech to text: one timed line per sentence
aistudio transcribe talk.m4a --vocabulary "Yuri Handmade" -o talk.txt   # names; plain text
aistudio flow credits
aistudio flow media --type video               # media ids in the "aistudio" Flow project
aistudio flow video "the apple starts rolling" --start-frame apple.jpg --i-have-rights --max-credits 10
aistudio flow extend <media-id> "the apple rolls off the table" --max-credits 10 -o next.mp4
aistudio flow edit <media-id> "make it black and white" --max-credits 20 -o bw.mp4
aistudio flow character create Robo "a friendly cartoon robot with round blue eyes" -o robot.png
aistudio flow character create Apple --photo <image-media-id>   # free: the photo becomes the character
aistudio flow character list
aistudio flow character personality <id> "calm and curious"     # free
aistudio flow character body <id> "full body, standing"         # 0 credits on Nano Banana 2.1
aistudio flow video "Robo waves at the camera" --character Robo  # quote; add --max-credits to make it
aistudio flow scene stitch <media-id> <media-id>@2-5 -o joined.mp4   # free; trims in whole seconds
aistudio flow scene download <scene-id> -o scene.mp4
aistudio flow frame save <media-id> -o last.jpg                 # free: the last frame, as a new image
```

</details>

## Several Google accounts

Each account lives in its own **profile**, with its own sign-in and its own Chrome profile:

```bash
aistudio profile create work
aistudio -p work login              # sign in with the other account
aistudio -p work generate image "a blue door" -o door.jpg
aistudio profile switch work        # make it the default
aistudio profile list               # * marks the active profile
```

A command uses the profile named by `-p`, else `$AISTUDIO_PROFILE`, else the one chosen with
`profile switch`, else `default`.

## Costs and what to expect

- **AI Studio quota.** `generate`, `transcribe`, `app create` and `app prompt` use your
  account's quota. Catalog prices on 2026-10-07 were about $0.034 per image, $0.04 per music clip
  (`lyria-3-clip-preview`) and $0.08 per full song (`-m lyria-3.5`).
- **Flow credits.** Nothing is spent without `--max-credits`. A run without it only shows
  Flow's quote. Veo 3.1 Lite is about 10 credits, Fast 20 and Quality 100. Free models (Nano
  Banana 2.1 images) generate at once and cost 0. Results go to a Flow project named "aistudio"
  unless you pass `--project`, and your Flow default settings are restored after each run.
- **`--i-have-rights`** answers Flow's "Rights to use this image: I agree" for a start frame or
  photo you upload. Pass it only when the statement is true.
- **Uploads.** Files given to `--image` or `transcribe` are uploaded to your Google Drive (AI
  Studio's folder) and stay there until you delete them. `transcribe` takes .mp3, .wav, .m4a,
  .flac, .ogg/.opus, .aac and .aiff, and audio-only .mp4/.webm up to 50 MB.
- **Not offered.** Chat with a model and Imagen need a Gemini API key or refuse this kind of
  session. Video in AI Studio (Veo) needs a paid upgrade there, so video goes through Flow
  instead. Flow avatars, Flow Music and Flow Tools are not offered either.
- **Things that need a one-time click in your own browser.** `prompt list` needs AI Studio to be
  allowed to use Google Drive: open https://aistudio.google.com/library once and press
  "Allow access". The CLI tells you when this is the problem.
- **Google can refuse a browser run.** Its anti-abuse checks sometimes reject the automated
  Chrome ("The caller does not have permission"). Waiting a while and re-running usually works.
  `--visible` shows the window.

## How it works

- **Reads** (lists, model cards, app code, incidents) are plain HTTPS calls to AI Studio's
  internal RPC endpoint, signed with your session cookies the way the web app signs them.
- **Generation and Build** drive AI Studio's own pages in a minimized Chrome window (Playwright),
  because those requests carry Google's anti-abuse tokens. Each run also refreshes the saved
  session that the plain HTTPS commands use.
- **Flow** is driven the same way.

Your session lives in `~/.aistudio/profiles/<name>/` (or `$AISTUDIO_HOME`), in folders only you
can read. `aistudio auth logout` deletes it. Nothing is sent anywhere except Google.

The design and the reverse-engineering notes are in
[`docs/superpowers/specs/2026-10-06-aistudio-cli-design.md`](docs/superpowers/specs/2026-10-06-aistudio-cli-design.md)
and [`docs/wire/findings.md`](docs/wire/findings.md).

## Use it from Claude Code

```bash
mkdir -p ~/.claude/skills/aistudio
cp integrations/claude-code/aistudio/SKILL.md ~/.claude/skills/aistudio/SKILL.md
```

Then, in any project, ask Claude things like "which music models do I have in AI Studio and what
do they cost?" or "list my AI Studio Build apps and their prompts". The `aistudio` skill makes
Claude run the CLI with `--json` against your own account.

## Development

```bash
uv sync --extra dev
uv run pytest -q                    # unit tests; replay recorded, redacted traffic
uv run ruff check src tests
uv run pytest -m live               # opt-in: hits the real service with your signed-in session
uv run aistudio dev capture         # record AI Studio traffic into tests/fixtures, redacted
```

The fixtures in `tests/fixtures` are real AI Studio responses, run through the redactor in
`src/aistudio/_dev/redact.py` (cookies, keys, tokens, names, emails, photo and account ids)
before they are saved. Check a new capture by eye before you commit it.

Bug reports and pull requests are welcome. AI Studio changes often, so a report should say
which command failed and include its `-vv` output. Remove cookies and personal data first.

## License

No license has been chosen yet.

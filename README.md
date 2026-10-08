# aistudio-py

Unofficial CLI and MCP server that automates the Google AI Studio web app
(https://aistudio.google.com) with your own Google session.

> **Warning:** this project uses undocumented internal endpoints of AI Studio. It can break
> whenever Google ships a change, and automating a personal Google session may violate
> Google's Terms of Service. Use a dedicated Google account, and use it at your own risk.

## Install (development)

```bash
brew install uv
uv sync --extra dev
```

## Quick start

```bash
uv run aistudio login                       # sign in once in a browser window
uv run aistudio auth check --test           # verify the stored session with one real call
uv run aistudio models list --category music
uv run aistudio models get lyria-3.5
uv run aistudio prompt list --type chat
uv run aistudio count-tokens "hello world" -m gemini-flash-latest
uv run aistudio app list                    # your Build apps
uv run aistudio app get <id-prefix>         # one app and its builder runs
uv run aistudio app create "a pomodoro timer with dark mode"   # builds in a minimized Chrome (~5-7 min)
uv run aistudio app prompt <id-prefix> "make the start button green"
uv run aistudio gallery list --tag google-maps
uv run aistudio status                      # AI Studio / Gemini API incidents
uv run aistudio models star lyria-3.5       # stars sync with the AI Studio picker
uv run aistudio app export <id-prefix> ./my-app   # download an app's code (verified)
uv run aistudio voices list
uv run aistudio generate speech "Hello! <laugh> Nice to meet you." --voice Tova --style "warm, slow" -o hello.wav
uv run aistudio generate image "a red apple on a white table" -o apple.jpg
uv run aistudio generate music "calm solo piano, slow tempo" -o piano.mp3
uv run aistudio flow credits
uv run aistudio flow video "a red apple rolling on a table" -m veo-3.1-lite --duration 8   # shows the quote
uv run aistudio flow video "a red apple rolling on a table" -m veo-3.1-lite --duration 8 --max-credits 10 -o apple.mp4
uv run aistudio flow media --type video                  # media ids in the "aistudio" project
uv run aistudio flow video "the apple starts rolling" --start-frame apple.jpg --i-have-rights --max-credits 10
uv run aistudio flow extend <media-id> "the apple rolls off the table" --max-credits 10 -o next.mp4
uv run aistudio flow character create Robo "a friendly cartoon robot with round blue eyes" -o robot.png
uv run aistudio flow character create Apple --photo <image-media-id>    # free: the photo becomes the character
uv run aistudio flow character list                     # ids and names; rename with "character rename ID NAME"
uv run aistudio flow character personality <id> "calm and curious"     # free
uv run aistudio flow character create Robo "a robot" -m nano-banana-pro  # or nano-banana-2-lite; 0 credits
uv run aistudio flow character body <id> "full body, standing"          # 0 credits on Nano Banana 2.1
uv run aistudio flow edit <media-id> "make it black and white" --max-credits 20 -o bw.mp4  # 8 s Veo 3.1 Lite clips
uv run aistudio flow video "Robo waves at the camera" --character Robo  # quote; add --max-credits to make it
uv run aistudio flow scene stitch <media-id> <media-id>@2-5 -o joined.mp4   # free; trims in whole seconds
uv run aistudio flow scene download <scene-id> -o scene.mp4              # e.g. the scene "flow extend" reports
uv run aistudio dev capture                 # record AI Studio traffic into tests/fixtures
```

Generation (`generate speech|image|music`) runs AI Studio's own page in a minimized Chrome window
and uses your account's quota. No API key is needed. Catalog prices on 2026-10-07 were about
$0.034 per image (`gemini-3.1-flash-lite-image`, the default), $0.04 per clip
(`lyria-3-clip-preview`, the default) and $0.08 per full song (`-m lyria-3.5`). Video (Veo) needs
a paid upgrade and Imagen refuses this kind of session, so neither is offered. Chatting with a
model needs a Gemini API key, so it is not offered either.

Video comes from Google Flow (`aistudio flow video|image`). Flow is paid in your Google AI
plan's Flow credits: Veo 3.1 Lite is about 10 credits, Fast 20 and Quality 100. **Nothing is spent
without `--max-credits`**: a run without it only shows Flow's quote (the free image model, Nano
Banana 2.1, generates at once and costs nothing; image models not measured yet are refused). When the quote fits the budget
and its counts match your options (model, aspect and outputs are set in Flow's own settings
first), aistudio approves it and downloads the result. Results go
to a Flow project named "aistudio", unless you pass `--project`. Your Flow default settings are
restored after each run.

Free models (Nano Banana 2.1 images) are generated at once: Flow does not ask, so there is no
quote, and aistudio downloads the result and reports the credits read before and after (0).
Avatars (a recorded selfie video), Flow Music (a separate site) and Tools (a builder) are not
offered.

Characters have names that are unique in their project, because Flow attaches them by name;
`--character` checks that Flow's request carries exactly the characters asked for before anything
can be approved. A character from a photo, renaming, listing and stitching clips are free.

Design: `docs/superpowers/specs/2026-10-06-aistudio-cli-design.md`

## Use from any Claude Code session

```bash
uv tool install --editable '.[browser]'      # puts `aistudio` on PATH (~/.local/bin); tracks this repo
mkdir -p ~/.claude/skills/aistudio
cp integrations/claude-code/aistudio/SKILL.md ~/.claude/skills/aistudio/SKILL.md
```

Then, in any project, ask Claude things like "which music models do I have in AI Studio and what
do they cost?" or "list my AI Studio Build apps and their prompts". The `aistudio` skill makes
Claude run the CLI with `--json` against your own account. Remove with
`uv tool uninstall aistudio-py` and `rm -r ~/.claude/skills/aistudio`.

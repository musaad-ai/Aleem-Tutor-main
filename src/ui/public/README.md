# `src/ui/public/` — Chainlit static assets

Chainlit serves any file in this folder under `/public/<filename>`. The
path is referenced from `.chainlit/config.toml`.

## Files

| File                     | Purpose                                                                  |
| ------------------------ | ------------------------------------------------------------------------ |
| `stylesheet.css`         | Aleem's CSS overrides — Arabic font loading and per-message auto-RTL.    |
| `aleem-logo.svg`         | Header wordmark + mark. Set as `logo_file_url` in the config.            |
| `aleem-avatar.svg`       | Assistant avatar. Set as `default_avatar_file_url` in the config.        |
| `elements/ContextChip.jsx` | `cl.CustomElement` for the pinned Grade·Subject chip (fixed pill).     |

## Design notes

`stylesheet.css` does **not** force `dir="rtl"` on the whole document.
Doing so would mirror the entire Chainlit chrome (buttons, settings
panel, scrollbars), which `BUILD_SPEC.md §4.8` calls out as undesirable
("proper mirroring, not just `direction: rtl`").

Instead it applies `unicode-bidi: plaintext` to message content blocks.
That lets each message take the direction of its dominant script: Arabic
paragraphs render RTL, English paragraphs render LTR, and mixed-language
paragraphs do the right thing per text run. The surrounding UI stays
LTR.

Fonts: **IBM Plex Sans Arabic** primary, **Cairo** fallback. Loaded from
Google Fonts at runtime — there are no font files checked in.

## Adding more assets

Logos, illustrations, favicons all go here. Reference them in
`chainlit.md` or via the config as `/public/<name>`. Keep files small;
this folder is served on every page load.

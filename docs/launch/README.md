# Launch materials

Everything needed to publish 0.3.0: recordable demo scripts (VHS tapes for
the terminal demos) and a draft announcement for Hacker News, r/Python, and
the tRPC Discord.

## Demo scripts

| Demo | Script | VHS tape |
| --- | --- | --- |
| ai-chat | [ai-chat.md](ai-chat.md) | [ai-chat.tape](ai-chat.tape) |
| live-types | [live-types.md](live-types.md) | [live-types.tape](live-types.tape) |
| migrate-from-node | [migrate-from-node.md](migrate-from-node.md) | (needs the 0.4.0 migration link) |

## Recording with VHS

[VHS](https://github.com/charmbracelet/vhs) replays a `.tape` file and emits
a GIF. Each tape below is self-contained: it builds a throwaway project in a
temp dir so the recording never depends on the repo checkout.

```bash
vhs docs/launch/live-types.tape   # produces live-types.gif
vhs docs/launch/ai-chat.tape      # produces ai-chat.gif
```

Recording checklist:

- [ ] Record at 1200×700 or larger, font ≥14px — the GIF text must stay legible in a feed.
- [ ] Use a clean terminal theme with high contrast.
- [ ] Keep each GIF under ~30 seconds; trim `Sleep`s if it drags.
- [ ] Show the *type change*, not just commands: the money shot is `.d.ts`
      updating after `save`, or SSE frames arriving live.
- [ ] Re-record after any UI/API change to the demo surface.

## Announcement

[announcement.md](announcement.md) — one draft adaptable to HN, r/Python,
and the tRPC Discord. Personalize the first line before posting.

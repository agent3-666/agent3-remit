# Storyboard, remit-demo.mp4

Silent, 1280x720, 30fps, 91 seconds. Rendered frame by frame from `site/demo.html` by
headless Chrome; no screen was recorded. Every number on screen comes from a live read of the
directory at build time, and the terminal beat is the real stdout of `python run.py`.

Built 2026-09-17 19:40 UTC against https://a2a-hub-chi.vercel.app, snapshot `ede78348d812414a8014f007ae3ce4ad9bcb7186f563fe94c5d51951809e48b2`.

| from | to | beat | what the picture says |
|---|---|---|---|
| 0:00 | 0:10 | `listing` | A directory lists a service and publishes a price for it. |
| 0:10 | 0:25 | `call` | Calling it with only what the directory publishes: 404, then the address the service names itself. |
| 0:25 | 0:40 | `blocked` | The same record, priced: the gateway is silent, the config endpoint is a disabled deployment, and no payee is published anywhere. |
| 0:40 | 0:54 | `terminal` | The real output of python run.py. |
| 0:54 | 1:09 | `quote` | What a payment would be for: the listing, the operation, the published price, and the snapshot it was read from. |
| 1:09 | 1:20 | `snapshot` | The snapshot digest, and how to recompute it. |
| 1:20 | 1:31 | `keys` | Two key names for the same thing, and why reading one is worse than it sounds. |

What the narration should carry, because the screen does not: that an agent is about to spend
real money on the strength of a listing, that a listing can be perfectly usable and still
impossible to pay, and that the honest output in that case is the name of the missing field.

What the narration must not say: that the directory is wrong, or that anybody published a
false address. What was observed is that the published address returned 404 and named other
endpoints, and that one of those answered.

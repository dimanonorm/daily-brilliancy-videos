# daily-brilliancy-videos

Automation for the Daily_Brilliancy chess Shorts channel.

- `videos/<name>.mp4` + `videos/<name>.json` (title, description, tags) → uploaded to YouTube by GitHub Actions when `youtube_upload` is `true` in `config.json`.
- Every run (on push and every 2 hours) posts the comment from `config.json` under recent channel videos that don't have it yet.
- Results: `state/last_run.md`, `state/uploaded.json`, `state/commented.json`.
- Secrets: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.

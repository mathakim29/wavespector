<div align="center">
    <h3>wavespector.ai</h3>
    <i>peel back the layers of your audio.</i>
</div>

---

## Why i made this
* i like to seperate music and use the instrumentals for personal use 
* my app, **wavespector.ai** gives you full freedom to test multiple models through [`audio-separator`](https://www.google.com/search?q=https://github.com/beverlis/audio-separator) and see for yourself the best results that you want
* Drop your file or paste a URL, swap between AI architectures, preview the waves, and grab your tracks
* Zero limits, total experimentation, and pristine results for your personal use


## Requirements

- node.js/npm or bun
- [uv](https://docs.astral.sh/uv/)
- `ffmpeg`

## Run locally

```bash
npm install
npm run dev
```

Open the frontend at <http://127.0.0.1:5173>. The API runs at
<http://127.0.0.1:8000>.

The first model run may download a model into `.asep/models`.

## Useful commands

```bash
npm/bun run dev       # Start frontend and API
npm/bun run dev:web   # Start only Vite
npm/bun run dev:api   # Start only the API
```

## API endpoints

- `GET /health` — Check API availability.
- `GET /models` — List available separation models.
- `POST /separate` — Submit an audio file or URL for processing.
- `GET /status/{job_id}` — Read job progress and logs.
- `GET /download/{job_id}/{filename}` — Download a generated stem.

Jobs run asynchronously. The frontend checks the API every three seconds and
automatically reconnects when the server becomes available again.

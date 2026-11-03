<script>
  import { onDestroy, onMount, tick } from 'svelte';
  import WaveSurfer from 'wavesurfer.js';
  import Hover from 'wavesurfer.js/dist/plugins/hover.esm.js';
  import Toastify from 'toastify-js';
  import 'toastify-js/src/toastify.css';

  const API_BASE = 'http://127.0.0.1:8000';
  let file = $state(null);
  let url = $state('');
  let models = $state([]);
  let selectedModel = $state('');
  let job = $state(null);
  let loadingModels = $state(true);
  let submitting = $state(false);
  let serverOnline = $state(false);
  let checkingServer = $state(true);
  let fileInput;
  let waveformContainers = [];
  let waveforms = [];

  function showError(message) {
    Toastify({
      text: message,
      duration: 4500,
      gravity: 'bottom',
      position: 'right',
      close: true,
      style: { background: '#27272a', border: '1px solid #71717a' }
    }).showToast();
  }

  function removeFile() {
    file = null;
    if (fileInput) fileInput.value = '';
  }

  async function checkServer() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2500);
    try {
      const response = await fetch(`${API_BASE}/health`, { signal: controller.signal });
      const wasOffline = !serverOnline;
      serverOnline = response.ok;
      if (response.ok && wasOffline && !checkingServer) {
        Toastify({ text: 'API reconnected', duration: 2500, gravity: 'bottom', position: 'right', style: { background: '#27272a', border: '1px solid #a1a1aa' } }).showToast();
      }
      if (response.ok && (!models.length || (wasOffline && !checkingServer))) await loadModels();
      if (!response.ok) serverOnline = false;
    } catch {
      serverOnline = false;
    } finally {
      clearTimeout(timeout);
      checkingServer = false;
    }
  }

  async function loadModels() {
    try {
      const response = await fetch(`${API_BASE}/models`);
      if (!response.ok) throw new Error('Could not load models');
      const catalog = await response.json();
      const seen = new Set();
      models = Object.values(catalog).flatMap((group) => Object.values(group)).filter((model) => model.filename && !seen.has(model.filename) && seen.add(model.filename));
      selectedModel = models[0]?.filename || '';
    } catch (err) { showError(err.message); }
    finally { loadingModels = false; }
  }

  async function separate() {
    if (!file && !url.trim()) return showError('Choose an audio file or enter a URL.');
    if (file && url.trim()) return showError('Use either a file or a URL, not both.');
    submitting = true; job = null;
    const form = new FormData();
    if (file) form.append('file', file);
    if (url.trim()) form.append('url', url.trim());
    form.append('model_name', selectedModel);
    try {
      const response = await fetch(`${API_BASE}/separate`, { method: 'POST', body: form });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Could not start separation');
      await poll(result.job_id);
    } catch (err) { showError(err.message); }
    finally { submitting = false; }
  }

  async function poll(jobId) {
    while (true) {
      const response = await fetch(`${API_BASE}/status/${jobId}`);
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Could not read job status');
      job = result;
      if (result.status === 'failed') {
        showError(result.error || 'Separation failed.');
        return;
      }
      if (result.status === 'done') return;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }

  async function createWaveforms() {
    waveforms.forEach((waveform) => waveform.destroy());
    waveforms = [];
    await tick();
    if (job?.status !== 'done') return;

    waveforms = (job.outputs || []).map((output, index) => {
      const name = output.split('/').pop();
      const waveform = WaveSurfer.create({
        container: waveformContainers[index],
        height: 64,
        waveColor: '#52525b',
        progressColor: '#f4f4f5',
        cursorColor: '#a1a1aa',
        barWidth: 2,
        barGap: 1,
        barRadius: 2,
        plugins: [Hover.create({
          lineColor: '#a1a1aa',
          lineWidth: 1,
          labelBackground: '#18181b',
          labelColor: '#f4f4f5',
          labelSize: '11px'
        })]
      });
      waveform.load(`${API_BASE}/download/${job.job_id}/${encodeURIComponent(name)}`);
      return waveform;
    });
  }

  function toggleWaveform(index) {
    waveforms[index]?.playPause();
  }

  $effect(() => {
    if (job?.status === 'done') createWaveforms();
  });

  onDestroy(() => waveforms.forEach((waveform) => waveform.destroy()));

  onMount(() => {
    checkServer();
    const interval = setInterval(checkServer, 3000);
    return () => clearInterval(interval);
  });
</script>



<svelte:head>
  <title>wavespector.ai</title>
  <meta name="description" content="Simple audio stem separation interface" />
</svelte:head>

<main class="min-h-screen px-4 py-12 text-zinc-100">
  <section class="mx-auto max-w-2xl bg-zinc-950 p-5">
    <header class="mb-8">
      <div class="flex items-center justify-between">
        <p class="mb-2 font-mono text-xs uppercase tracking-[0.3em] text-zinc-300">wavespector.ai</p>
        <span class="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-zinc-500">
          <span class:animate-pulse={!serverOnline} class={`h-2 w-2 rounded-full ${serverOnline ? 'bg-zinc-100' : 'bg-zinc-600'}`}></span>
          {checkingServer ? 'connecting' : serverOnline ? 'api online' : 'api offline'}
        </span>
      </div>
      <h1 class="text-3xl font-semibold tracking-tight">Separate your track</h1>
      <p class="mt-2 text-sm text-zinc-400">Upload audio or paste a public URL to create downloadable stems.</p>
    </header>

    <form onsubmit={(event) => { event.preventDefault(); separate(); }} class="space-y-5">
      <label class="block space-y-2"><span class="text-sm font-medium">Audio file</span>
        <div class="flex items-center gap-2">
          <input bind:this={fileInput} type="file" accept="audio/*" onchange={(event) => { file = event.currentTarget.files[0] || null; }} class="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-zinc-100 file:px-3 file:py-1.5 file:text-zinc-950" />
          {#if file}
            <button type="button" aria-label="Remove selected file" title="Remove selected file" onclick={removeFile} class="shrink-0 rounded-lg border border-zinc-700 px-3 py-2 text-lg leading-none text-zinc-300 hover:border-zinc-300 hover:text-white">×</button>
          {/if}
        </div>
        {#if file}<p class="truncate text-xs text-zinc-500">Selected: {file.name}</p>{/if}
      </label>
      <div class="flex items-center gap-3 text-xs uppercase tracking-widest text-zinc-600"><span class="h-px flex-1 bg-zinc-800"></span>or<span class="h-px flex-1 bg-zinc-800"></span></div>
      <label class="block space-y-2"><span class="text-sm font-medium">Audio URL (from YouTube, etc..)</span>
        <input bind:value={url} type="url" placeholder="https://..." class="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none ring-zinc-300 focus:ring-2" />
      </label>
      <label class="block space-y-2"><span class="text-sm font-medium">Model</span>
        <select bind:value={selectedModel} disabled={loadingModels || !models.length} class="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none ring-zinc-300 focus:ring-2">
          {#if loadingModels}<option>Loading models...</option>{/if}
          {#each models as model}<option value={model.filename}>{model.filename}</option>{/each}
        </select>
      </label>
      <button type="submit" disabled={submitting || !serverOnline || loadingModels || !selectedModel} class="w-full rounded-lg bg-zinc-100 px-4 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50">{submitting ? 'Separating...' : !serverOnline ? 'Waiting for API...' : 'Separate audio'}</button>
    </form>

    {#if job}
      <section class="mt-6">
        <div class="flex items-center justify-between"><h2 class="font-medium">Job status</h2><span class="font-mono text-xs uppercase text-zinc-300">{job.status}</span></div>
        {#if job.status !== 'done' && job.status !== 'failed'}
          <div class="mt-4 h-2 overflow-hidden rounded-full bg-zinc-800"><div class="h-full bg-zinc-100 transition-all" style={`width: ${job.progress || 10}%`}></div></div>
        {:else if job.status === 'done'}
          <div class="mt-4 space-y-4">
            {#each job.outputs || [] as output, index}
              {@const name = output.split('/').pop()}
              <article class="rounded-lg border border-zinc-800 p-3">
                <div class="mb-2 flex items-center justify-between gap-3 text-sm">
                  <span class="truncate">{name}</span>
                  <a href={`${API_BASE}/download/${job.job_id}/${encodeURIComponent(name)}`} download class="shrink-0 text-xs text-zinc-300 hover:text-white">Download</a>
                </div>
                <div bind:this={waveformContainers[index]} class="min-h-16 rounded bg-zinc-950"></div>
                <button type="button" onclick={() => toggleWaveform(index)} class="mt-2 rounded border border-zinc-700 px-3 py-1 text-xs hover:border-zinc-300">Play / pause</button>
              </article>
            {/each}
          </div>
        {/if}
        {#if job.logs?.length}
          <details class="mt-5 rounded-lg border border-zinc-800 bg-zinc-950" open>
            <summary class="cursor-pointer px-3 py-2 text-xs uppercase tracking-widest text-zinc-500">API console</summary>
            <div class="max-h-48 overflow-y-auto border-t border-zinc-800 px-3 py-2 font-mono text-xs text-zinc-400">
              {#each job.logs as log}<p class="py-0.5">{log}</p>{/each}
            </div>
          </details>
        {/if}
      </section>
    {/if}
  </section>
</main>


<style>
  @import "tailwindcss";
  :global(body) {
    background-image: url('https://file.garden/ZWlUCY4S7Xz2vypS/archived%20backgrounds/colours/blue/blue042.jpg');
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
    background-attachment: fixed;
    min-height: 100vh;
    margin: 0;
  }
</style>

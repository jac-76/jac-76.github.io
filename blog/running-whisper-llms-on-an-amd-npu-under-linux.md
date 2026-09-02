---
title: "Running Whisper + LLMs on an AMD NPU under Linux"
slug: running-whisper-llms-on-an-amd-npu-under-linux
description: "Deploying whisper-large-v3-turbo and LLMs on an AMD Ryzen AI (XDNA2) NPU under Arch Linux, end to end — driver stack, the memlock gotcha, FastFlowLM, and a measured NPU-vs-CPU energy comparison."
author: jac-76
date: 2026-09-02
canonical: https://jac-76.github.io/blog/running-whisper-llms-on-an-amd-npu-under-linux.html
tags: [amd, ryzenai, npu, whisper, linux]
---

# Running Whisper + LLMs on an AMD NPU under Linux

> **TL;DR** — On a MSI Stealth A16 AI+ (Ryzen AI 9 365, XDNA2 NPU) running Arch,
> I got OpenAI's `whisper-large-v3-turbo` transcribing on the **NPU** — not the
> CPU, not the GPU — at **RTF ≈ 0.18** (a 30 s clip in ~5.2 s) for roughly a
> **tenth of the energy** the same job costs on the CPU, plus an LLM answering
> on the same NPU through an OpenAI-compatible API. The
> whole path is local and offline. This is the write-up of the driver stack,
> the one real gotcha (memlock), and the runtime that made it a 20-minute job
> instead of a weekend.

---

## Why this is worth writing down

AMD's "Ryzen AI" NPU (the XDNA / XDNA2 block in Phoenix / Hawk Point / Strix
Point laptops) is marketed almost entirely around Windows: the Ryzen AI SDK,
the ONNX Runtime **VitisAI** execution provider, Lemonade, and the demos all
assume you're on Windows with the official stack. On Linux the picture in early
2026 is better than most people think — the NPU driver has been **in the
mainline kernel** as `amdxdna` since 6.14 — but the "load a real model and run
it" story still isn't well documented.

Here's what actually worked, end to end.

### The hardware

| Part | Detail |
|---|---|
| Laptop | MSI Stealth A16 AI+ A3HVGG |
| APU | AMD Ryzen AI 9 365 (Strix Point) |
| NPU | XDNA2, 8 columns, exposed as `/dev/accel/accel0` |
| NPU firmware | `1.1.2.64` |
| Kernel | 7.1.9-arch1 (`amdxdna` in-tree) |
| OS | Omarchy (Arch Linux) |

AMD quotes the Strix Point NPU at [up to 50 TOPS, INT8](https://www.amd.com/en/products/processors/laptop/ryzen/ai-300-series/amd-ryzen-ai-9-365.html).

---

## 1. The driver stack

Three pieces have to be in place before any runtime can touch the NPU:

1. **`amdxdna`** — the kernel driver. In-tree from Linux 6.14; it's what creates
   `/dev/accel/accel0`. Check it's bound:

   ```console
   $ ls /dev/accel/
   accel0
   $ dmesg | grep -i amdxdna
   ```

2. **XRT** (Xilinx/AMD Runtime) + the **`xrt-plugin-amdxdna`** shim. XRT is the
   userspace API; the plugin teaches it about the XDNA device. On Arch both are
   in `extra`:

   ```console
   $ sudo pacman -S xrt xrt-plugin-amdxdna
   $ xrt-smi examine
   ...
   XRT
     Version              : 2.21.75
     NPU Firmware Version  : 1.1.2.64

   Device(s) Present
   |BDF             |Name          |
   |----------------|--------------|
   |[0000:66:00.1]  |RyzenAI-npu4  |
   ```

   You want a `Device(s) Present` line with a `RyzenAI-npu*` name. If XRT is
   installed but the plugin isn't, `xrt-smi` runs but that table is empty.
   `xrt-smi examine --report platform` then shows `Total Columns : 8` — the
   XDNA2 array this SoC exposes.

3. **Versions that worked here:** `xrt 2.21.75`, `xrt-plugin-amdxdna` (same
   release), NPU firmware `1.1.2.64`, and `flm validate` reporting the `amdxdna`
   driver interface as **0.8**.

### The one real gotcha: memlock

The NPU runtime pins model weights into physical RAM, so the calling user needs
an **unlimited memlock rlimit**. The default (usually 8 MiB or 64 MiB) is nowhere
near enough and the failure mode is an unhelpful allocation error deep in the
runtime.

```console
$ sudo tee -a /etc/security/limits.conf <<< "$USER soft memlock unlimited"
$ sudo tee -a /etc/security/limits.conf <<< "$USER hard memlock unlimited"
# log out and back in
```

You want to see this afterwards:

```console
$ ulimit -l
unlimited
```

---

## 2. The shortcut: FastFlowLM instead of the Ryzen AI SDK

The "official" Linux route is: build ONNX Runtime with the VitisAI EP, install
the Ryzen AI SDK bits, quantize your model to the NPU's format, wrangle a Python
venv full of `onnxruntime-vitisai` and Vitis tooling. It's a lot, and much of it
is Windows-first.

**[FastFlowLM](https://fastflowlm.com/)** (`flm`) skips all of that. It's a
NPU-first runtime (Rust/C++) that ships **prebuilt xclbins** (the NPU binary
kernels) and `libwhisper_npu.so` in the package itself:

```console
$ ls /usr/share/flm/xclbins/
encoder_attn  encoder_dequant  encoder_mm  whisper_head  ...
$ flm --version
FLM v1.0.2
```

Because the kernels are bundled, **there is no onnxruntime-vitisai / Ryzen AI SDK
venv to build**. (Arch's stock `python-onnxruntime-cpu` only has
`CPUExecutionProvider` anyway — irrelevant here.) On Arch: `sudo pacman -S
fastflowlm`.

Validate the whole stack in one shot:

```console
$ flm validate
[Linux]  Kernel: 7.1.9-arch1-2
[Linux]  NPU: /dev/accel/accel0 with 8 columns
[Linux]  NPU FW Version: 1.1.2.64
[Linux]  amdxdna version: 0.8
[Linux]  Memlock Limit: infinity
```

All green = ready. If `Memlock Limit` says anything other than `infinity`, go
back to the limits.conf step.

---

## 3. Whisper on the NPU

Pull the model — `whisper-v3:turbo` is `large-v3-turbo` quantized for XDNA2:

```console
$ flm pull whisper-v3:turbo
# ~650 MB: model.q4nx + tokenizers -> ~/.config/flm/models/Whisper-V3-Turbo-NPU2/
```

Serve it. On FLM 1.0.2+ Whisper loads **standalone** — older docs claimed you had
to co-load an LLM, but you don't:

```console
$ flm serve --asr 1          # OpenAI-compatible server on :52625
```

Transcribe over the HTTP API (anything `ffmpeg` can decode — wav/mp3/ogg/m4a/flac):

```console
$ curl http://127.0.0.1:52625/v1/audio/transcriptions \
    -F "file=@audio.ogg" \
    -F "model=whisper-v3"
```

There's also a CLI path: `flm run <model> --asr 1`, then `/input "clip.mp3"` in
the chat prompt.

### Results

Benchmarked with the bundled `bench.py` — 10 runs, first 2 discarded as warm-up,
audio length read from the file via `ffprobe` so the RTF is honest and
reproducible:

| Metric | Value |
|---|---|
| Audio length | 30.0 s (JFK, Rice University speech excerpt) |
| Transcription wall time (warm) | **5.2 s** (σ 0.04 s within a run; 5.17–5.6 s across sessions) |
| **Real-time factor (RTF)** | **≈ 0.17–0.19** |
| Transcript accuracy | correct, verbatim |

Roughly **5–6× faster than real time**. Within a single benchmark the spread is
under 1%; between sessions the mean drifts a few hundred ms with machine
temperature and background load.

### Is it actually on the NPU?

Two checks. First, the FLM log prints `[NPU Locked!]` when a job starts and
`[NPU Lock Released!]` when it finishes. Second — and more convincing — sample
system load while the benchmark runs and see that nothing else is doing the
work:

| Device | Idle baseline | During 10 transcriptions |
|---|---|---|
| CPU (20 threads, system-wide) | 2.2 % | **4.3 %** mean, 14.4 % peak |
| iGPU (Radeon 890M) | 7 % | **10 %** mean, 15 % peak |
| dGPU (RTX 4070) | 0 % | **0 %** |

The CPU rises about two points over idle — that's the `curl`/harness overhead and
the server's I/O thread, not inference. The iGPU delta is desktop compositing
(Hyprland renders on the 890M), and the discrete GPU is never touched at all.
The 30 seconds of audio is being processed somewhere that doesn't show up in any
of these three counters, which is exactly the point: the CPU and both GPUs stay
free while the NPU works.

<!-- TODO: screenshot of the FLM log showing the [NPU Locked!] / [NPU Lock
Released!] lines, to sit beside the load table above. -->

### What the NPU actually buys you: energy

Speed alone isn't the story — `whisper-large-v3-turbo` has a tiny decoder and
runs fine on CPU. So I built `whisper.cpp` from source (the Arch package's ggml
backend is currently broken) and ran the **same 30 s clip through the same
model** on the CPU, tuned to 16 threads, reading the RAPL energy counters
(`/sys/class/powercap/intel-rapl:0`) around every run.

| | **NPU** (FastFlowLM) | **CPU** (whisper.cpp, `-t 16`) |
|---|---|---|
| Wall time (30 s clip) | ~5.3 s | ~6.5 s |
| RTF | 0.18 | 0.22 |
| CPU-package power while running | ~20 W | ~73 W |
| CPU-*core* power while running | ~0.8 W | ~10 W |
| **Energy per transcription, over idle** | **~45 J** | **~410 J** |
| Energy per transcription, total package | ~105 J | ~478 J |

The wall-clock win is modest — about 25%. The **energy** difference is the
point: transcribing that clip on the NPU costs roughly **an order of magnitude
less energy** than doing it on the CPU (~45 J vs ~410 J above idle). Package
power rises ~10 W instead of ~60 W, the CPU cores never leave idle, and the fans
stay quiet. Per hundred transcriptions that's about 1 W·h versus 11 W·h — and 20
CPU threads left free the whole time.

*(Measured on a live desktop, so absolute wattages drift a few watts between
runs with background activity — "energy over idle" is the stable figure and what
the comparison rests on. `whisper-cli` also reloads the 1.6 GB model each run,
which pads its wall time slightly but not its energy. Both harnesses are in the
[npu-whisper](https://github.com/jac-76/npu-whisper) repo:
`bench.py --power` for the NPU column, `bench_cpu.py` for the CPU column.)*

---

## 4. An LLM on the same NPU

FLM serves LLMs on the NPU through the same OpenAI-compatible surface. Its model
catalogue covers the usual small-to-mid open weights:

```console
$ flm list
  gemma3:1b        ✅
  qwen3:1.7b       ⏬
  llama3.2:3b      ⏬
  phi4-mini-it:4b  ⏬
  deepseek-r1:8b   ⏬
  gpt-oss:20b      ⏬
  whisper-v3:turbo ✅
  ...
```

One server can expose **both** ASR and chat:

```console
$ flm serve gemma3:1b --asr 1
$ curl http://127.0.0.1:52625/v1/chat/completions \
    -H 'content-type: application/json' \
    -d '{"model":"gemma3:1b","messages":[{"role":"user","content":"hello"}]}'
```

So `/v1/audio/transcriptions` and `/v1/chat/completions` are both live on
`:52625` from a single process on the NPU.

---

## 5. Tying it together — two small tools

With the endpoint working, the rest is glue:

- **[`npu-whisper`](https://github.com/jac-76/npu-whisper)** — a zero-dependency
  Bash wrapper: run it on an audio file and it auto-starts `flm serve --asr 1`
  if it's down, waits for readiness, `curl`s the transcript, and leaves the
  server warm. `--json`, `--status`, `--stop`.

- **[`local-ai-assistant`](https://github.com/jac-76/local-ai-assistant)** — a
  fully offline voice assistant, stdlib-only Python:
  `pw-record → Whisper (NPU) → LLM (NPU) → piper TTS → speaker`. One FLM server
  backs the whole chain. `chat` keeps conversation history across runs.

Both are deliberately small — the interesting work was getting the NPU to do the
inference, not the plumbing on top.

---

## Gotchas & rough edges

- **memlock** is the thing that bites everyone first. `flm validate` catches it.
- **FLM version:** 1.0.2 here; 1.0.3 exists but the update notice points at a
  Windows `.msi` — 1.0.2 is fine to stay on for Linux.
- **Model format is runtime-specific.** These `.q4nx` weights + bundled xclbins
  are FastFlowLM's; you can't point llama.cpp or vanilla ONNX Runtime at them.
- **No GPU/CPU fallback worth using.** If the NPU path fails, you're better off
  fixing it than limping along on CPU — `whisper.cpp` on CPU is far slower for
  `large-v3-turbo`.
- **Discoverability.** Almost every AMD NPU tutorial is Windows. The Linux
  `amdxdna` + XRT + FLM combination works well but you have to assemble it
  yourself. That's the gap this post is trying to close.

---

## Reproduce it

```console
# 1. driver stack
sudo pacman -S xrt xrt-plugin-amdxdna fastflowlm
sudo tee -a /etc/security/limits.conf <<< "$USER soft memlock unlimited"
sudo tee -a /etc/security/limits.conf <<< "$USER hard memlock unlimited"
# log out / back in
flm validate            # want: all green, Memlock Limit: infinity

# 2. models
flm pull whisper-v3:turbo
flm pull gemma3:1b

# 3. run
flm serve gemma3:1b --asr 1
curl http://127.0.0.1:52625/v1/audio/transcriptions -F file=@clip.wav -F model=whisper-v3
```

Requirements: a Ryzen AI (XDNA / XDNA2) laptop, kernel ≥ 6.14 with `amdxdna`,
and the memlock bump.

---

<!--
DRAFT NOTES for jac-76 — before publishing:
- Benchmark: DONE. History — opencode 2026-09-01 added `bench.py` and reported
  5.34 s ± 0.001 s (but that σ came from only n=2 warm samples). claudecode
  re-ran twice: n=2 → 5.25 s σ0.017; **n=8 → 5.167 s σ0.042 s, RTF 0.172** —
  that 10-run figure is what the post now quotes. Also measured CPU/iGPU/dGPU
  load during the runs (the post previously *asserted* "CPU/GPU idle" without
  measuring — now it's a real table). Earlier drafts said 28.5 s / 5.4 s /
  RTF 0.19; the clip is actually 30.0 s. All corrected.
- TOPS claim: DONE 2026-09-01 — keeping "up to 50 TOPS (INT8)", cited to AMD's
  official Ryzen AI 9 365 page.
- xrt-smi examine: DONE 2026-09-02 — real device line (`[0000:66:00.1]
  RyzenAI-npu4`) + firmware + 8-column note now in §1.
- RAPL power comparison: DONE 2026-09-01/02 — NPU vs CPU (whisper.cpp) energy
  table is in §3. (Earlier note said "deferred"; the user reopened it.)
- STILL OPEN (visual polish, not blocking publish):
    * screenshot of the FLM log ([NPU Locked!] lines) next to the load table
    * 30–60 s demo GIF of local-ai-assistant (voice → reply) — CAREER.md Phase 2
  These can be added after the post goes live.
- Publish: Dev.to canonical → cross-post r/LocalLLaMA, r/linux, r/RyzenAI, HN.
  Link the post from jac-76.github.io (the `# flagship_writeup` section has a
  placeholder for the URL).
-->

# Spark voice server

Runs next to vLLM on the DGX Spark. Gives the bot local transcription, local speech and local
memory embeddings, so with `SPARK_URL` and `SPARK_VOICE_URL` set nothing personal leaves the
house: Fireworks only ever sees the schedule and todos, and only while the Spark is down.

Not yet run on the Spark itself. The endpoints match what the bot calls (the bot side is tested
against fakes), the engines are the usual libraries. Expect to fix a dependency or two the first
time on arm64 + CUDA.

## Memory

vLLM already owns most of the Spark's 121 GiB, and the 26 GiB host reserve is what keeps the
kernel from hanging. So this server runs on CPU by default (`VOICE_DEVICE=cpu`, Whisper
`small`): a few hundred MB of RAM, a few seconds per voice note on the 20 ARM cores, and it
never touches the GPU budget. Switch to `VOICE_DEVICE=cuda` and `WHISPER_MODEL=large-v3-turbo`
only after checking `free -h` leaves the reserve intact with vLLM up.

## Install (on the Spark, once)

```bash
git clone git@github.com:giopelagalli/telegramManager.git ~/telegramManager   # needs the Spark's deploy key
cd ~/telegramManager/spark
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo apt install -y ffmpeg
mkdir -p models && ../scripts/download_voice_models.sh ./models     # the two Kokoro files
sed -e "s|__USER__|$USER|g" -e "s|__ROOT__|$HOME/telegramManager|g" voice-server.service \
  | sudo tee /etc/systemd/system/voice-server.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now voice-server
curl -s http://localhost:8890/v1/models
```

First call to each endpoint loads its model (whisper ~1.5GB, nomic ~0.5GB), so the first voice
note takes a while; after that it is quick.

## Reaching it from the droplet

Both machines on the same tailnet (`tailscale status` on each). vLLM's container may be bound to
`localhost` only; check on the Spark:

```bash
ss -ltnp | grep -E ':8888|:8890'
```

`0.0.0.0:8888` means the droplet can reach it at `http://spark-f9a9:8888/v1`. `127.0.0.1:8888`
means it can't; the least invasive fix is to let Tailscale proxy it without touching `start.sh`:

```bash
sudo tailscale serve --bg --http=8888 http://127.0.0.1:8888
```

(`tailscale serve --help` if the flags differ on your version.) The voice server binds
`0.0.0.0` itself.

## Point the droplet at it

In the droplet's `.env`:

```
SPARK_URL=http://spark-f9a9:8888/v1
SPARK_VOICE_URL=http://spark-f9a9:8890/v1
```

Then `sudo systemctl restart assistant`. Check from the droplet:

```bash
curl -s http://spark-f9a9:8890/v1/models
```

## A more human voice

Kokoro is the default because it installs anywhere. For a voice that sounds like a person,
install Chatterbox on the Spark and switch the engine:

```bash
.venv/bin/pip install chatterbox-tts
sudo systemctl edit voice-server   # add: Environment=TTS_ENGINE=chatterbox
sudo systemctl restart voice-server
```

The bot needs no change: `TTS_MODEL` is only a label.

## The old `sparkbot`

Retire it: `sudo systemctl disable --now sparkbot`. JD is the only bot. The model service
(`sparkmodel`) stays and autostarts on boot; that is all the Spark needs to run.

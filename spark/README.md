# Spark voice server

Runs next to vLLM on the DGX Spark. Gives the bot local transcription, local speech and local
memory embeddings, so with `SPARK_URL` and `SPARK_VOICE_URL` set nothing personal leaves the
house: Fireworks only ever sees the schedule and todos, and only while the Spark is down.

Not yet run on the Spark itself. The endpoints match what the bot calls (the bot side is tested
against fakes), the engines are the usual libraries. Expect to fix a dependency or two the first
time on arm64 + CUDA.

## Install (on the Spark, once)

```bash
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

## Point the droplet at it

In the droplet's `.env`:

```
SPARK_URL=http://<spark-tailscale-name>:8888/v1
SPARK_VOICE_URL=http://<spark-tailscale-name>:8890/v1
```

Then `sudo systemctl restart assistant`. Check from the droplet:

```bash
curl -s http://<spark-tailscale-name>:8890/v1/models
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

# macOS push-to-talk

Jarvis can gate microphone audio with a system-wide press-and-hold hotkey. The
native helper uses the local PyObjC/Quartz dependencies already installed by the
server; it does not use a cloud service.

## Enable and run

1. Set `hotkey.enabled: true` in `server/config.yaml`, then restart `bot.py`.
2. Connect the web client and allow microphone access as usual.
3. In another terminal, run:

   ```shell
   cd server
   uv run hotkey_helper.py
   ```

The default is the physical **Right Command (⌘)** key. Hold it to pass microphone
audio to STT/VAD and release it to replace microphone frames with silence.
Always-on conversation remains unchanged while `hotkey.enabled` is `false`.

On first run, macOS prompts for Accessibility access. Enable the terminal app
(or the Python executable that launches the helper) under **System Settings →
Privacy & Security → Accessibility**. If creation of the event tap is still
blocked, enable it under **Input Monitoring** too. Quit and restart the helper
after changing either permission.

## Customize

Edit `server/config.yaml`:

```yaml
hotkey:
  enabled: true
  key: command+space
  server_url: http://127.0.0.1:7860
```

Supported key names are `right_command`, `left_command`, letters, digits,
`space`, `return`, `tab`, `delete`, `escape`, and arrow keys. Chord modifiers
are `command`/`cmd`, `control`/`ctrl`, `option`/`alt`, and `shift`. Avoid macOS
shortcuts such as `command+space` unless you first reassign Spotlight.

Environment overrides are `HOTKEY_ENABLED`, `HOTKEY_KEY`, and
`HOTKEY_SERVER_URL`. The helper also accepts one-run `--key` and `--server-url`
arguments. File changes persist; environment and command-line overrides do not.

## Safety and troubleshooting

- The helper sends a heartbeat while held. The server closes the audio gate
  within 1.5 seconds if the helper exits, loses permission, or loses contact.
- Normal release and Ctrl-C send an immediate closed state.
- `HTTP Error 409` means the server was not restarted after enabling PTT.
- `Connection refused` means `bot.py` is not listening at `server_url`.
- If the key works only in some apps, re-check both Accessibility and Input
  Monitoring permissions and restart the helper.
- Check state with `curl http://127.0.0.1:7860/api/ptt`.

## M-series manual smoke test (LAP0014-class)

1. On an Apple Silicon Mac, enable PTT, start the stack and helper, and connect
   Chrome or Safari.
2. With another application focused, hold Right Command and speak. Confirm one
   user turn is transcribed.
3. Release Right Command and speak again. Confirm no VAD turn or transcription.
4. Hold the key, then terminate the helper. After 1.5 seconds, confirm speech no
   longer reaches VAD.
5. Restart with `HOTKEY_KEY=control+space`, repeat steps 2–3, then disable
   `hotkey.enabled` and confirm the original always-on mode still works.

The automated parser tests run cross-platform; Quartz capture and macOS TCC
permissions require this manual hardware test.

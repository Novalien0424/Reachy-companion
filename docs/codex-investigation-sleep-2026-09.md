# Codex Investigation: Sleep Shutdown Failure (2026-09)

## Scope

- DOCUMENTED: This report is based on the requested files only, plus `tests/` grep. The root `tests/` directory was not present.
- DOCUMENTED: The handoff describes the signature as identical in v1.19/v1.20: no `Requested current app stop via ...`, C6 mic-unmute about 7s after `Stopping movement manager`, no pose-failure log, and `Sleep summary failed: TimeoutError` (`session-handoff.md:41`-`session-handoff.md:52`).

## Finding 1: Stop Request Exception Taxonomy

- DOCUMENTED: `request_stop_current_app()` builds `http://{robot.client.host}:{robot.client.port}/api/apps/stop-current-app`, sends a POST with timeout `2.0`, then calls `response.read()` before logging success (`reachy_companion/src/reachy_companion/app_lifecycle.py:20`-`reachy_companion/src/reachy_companion/app_lifecycle.py:38`).
- DOCUMENTED: The only handled exception is `urllib.error.URLError`; success is logged only after `response.read()` returns (`reachy_companion/src/reachy_companion/app_lifecycle.py:30`-`reachy_companion/src/reachy_companion/app_lifecycle.py:37`).
- DOCUMENTED: Local Python class verification: `urllib.error.HTTPError` is a `URLError`, so HTTP error responses raised as `HTTPError` are caught.
- DOCUMENTED: Local Python class verification: `http.client.RemoteDisconnected` MRO is `RemoteDisconnected -> ConnectionResetError -> ConnectionError -> OSError -> BadStatusLine -> HTTPException -> Exception -> BaseException -> object`.
- DOCUMENTED: Therefore `RemoteDisconnected` is a subclass of both `ConnectionResetError` and `BadStatusLine`, but `issubclass(RemoteDisconnected, urllib.error.URLError) == False`.
- DOCUMENTED: Other relevant exceptions not caught by `except urllib.error.URLError` include `http.client.BadStatusLine`, `http.client.IncompleteRead`, `http.client.LineTooLong`, generic `http.client.HTTPException`, raw `TimeoutError`/`socket.timeout`, and raw `ConnectionResetError`/`BrokenPipeError`/`ConnectionAbortedError`.
- INFERRED: `urlopen()` wraps many connect/send `OSError`s as `URLError`, but exceptions from `getresponse()` and from `response.read()` can escape as `http.client.*` or raw `OSError` subclasses. A daemon that accepts the stop POST and closes before a complete HTTP response can therefore bypass the guard.
- INFERRED: The theory is confirmed as a valid code-path explanation, but not proven as the exact runtime exception without a traceback. The absent success log is exactly what would happen if the exception happened before or during `response.read()`.

## Finding 2: Worker Call Order

- DOCUMENTED: The `go_to_sleep` tool itself does not pose the robot. It mutes/disarms via `deps.begin_sleep()` and returns `{"status": "sleeping_soon", ...}` (`reachy_companion/src/reachy_companion/tools/go_to_sleep.py:56`-`reachy_companion/src/reachy_companion/tools/go_to_sleep.py:83`).
- DOCUMENTED: The realtime handler treats a result as session-ending only when the tool has `ends_session` and the result status is exactly `sleeping_soon` (`reachy_companion/src/reachy_companion/huggingface_realtime.py:3303`-`reachy_companion/src/reachy_companion/huggingface_realtime.py:3313`).
- DOCUMENTED: If the function-call result reaches the model, the handler waits for one farewell response and then finalizes sleep; if the result cannot be submitted, fallback paths still run the finalizer without the farewell (`reachy_companion/src/reachy_companion/huggingface_realtime.py:3155`-`reachy_companion/src/reachy_companion/huggingface_realtime.py:3297`).
- DOCUMENTED: `_finish_session_after_farewell()` awaits `run_farewell_response_cycle()` and then `_finalize_session_sleep()` (`reachy_companion/src/reachy_companion/huggingface_realtime.py:3315`-`reachy_companion/src/reachy_companion/huggingface_realtime.py:3327`).
- DOCUMENTED: `_finalize_session_sleep()` calls `deps.go_to_sleep` in `asyncio.to_thread()` and catches/logs any exception as `sleep: the sleep callback failed` (`reachy_companion/src/reachy_companion/huggingface_realtime.py:3329`-`reachy_companion/src/reachy_companion/huggingface_realtime.py:3346`).
- DOCUMENTED: On that worker path, order is: repeat sleep quiesce, wait for speaker quiet, disable wobbling, `movement_manager.stop(reset_to_neutral=False)`, `robot.goto_sleep()`, `request_stop_current_app()`, then local stop via `app_stop_event.set()` or `stream_manager.close()` (`reachy_companion/src/reachy_companion/main.py:347`-`reachy_companion/src/reachy_companion/main.py:386`).
- DOCUMENTED: `robot.goto_sleep()` exceptions are caught and logged as `Failed to move Reachy Mini to sleep pose`, then the stop request still proceeds (`reachy_companion/src/reachy_companion/main.py:360`-`reachy_companion/src/reachy_companion/main.py:368`).
- DOCUMENTED: The C6 outer handler catches exceptions from the broader block, unmutes the microphone, logs `go_to_sleep failed before the stop; microphone unmuted`, and re-raises (`reachy_companion/src/reachy_companion/main.py:387`-`reachy_companion/src/reachy_companion/main.py:408`).
- INFERRED: Because pose exceptions are caught locally, a C6 log after the pose line is unlikely to be caused by `robot.goto_sleep()` itself. Plausible remaining sources include unguarded `movement_manager.stop()` and non-`URLError` failures from `request_stop_current_app()`.
- INFERRED: The journal timing after `Stopping movement manager` plus no pose-failure log supports, but does not conclusively prove, the stop-request response-path failure. Physical confirmation that the robot reached sleep pose would make the stop-request theory much stronger.

## Finding 3: Sleep Summary Ordering And Timeout

- DOCUMENTED: Sleep summary is not part of the worker finalizer shown above. It runs during realtime handler shutdown when `deps.sleep_requested` is true and `_sleep_summary_done` is false (`reachy_companion/src/reachy_companion/huggingface_realtime.py:4151`-`reachy_companion/src/reachy_companion/huggingface_realtime.py:4164`).
- DOCUMENTED: In that shutdown branch, summary runs after `on_session_shutdown(...)` and before `connection.close()` (`reachy_companion/src/reachy_companion/huggingface_realtime.py:4148`-`reachy_companion/src/reachy_companion/huggingface_realtime.py:4162`).
- DOCUMENTED: `write_sleep_summaries()` builds its own client with `reachy_companion.hanova.images.build_client()` when no client is passed (`reachy_companion/src/reachy_companion/sleep_summary.py:154`-`reachy_companion/src/reachy_companion/sleep_summary.py:157`).
- DOCUMENTED: `sleep_summary.py` itself sets no host and no port. Those are hidden behind `build_client()`, which was outside the allowed read scope.
- DOCUMENTED: `sleep_summary.py` uses model env `MEMORY_LAST_CHAT_MODEL` defaulting to `gpt-5-mini` (`reachy_companion/src/reachy_companion/sleep_summary.py:88`-`reachy_companion/src/reachy_companion/sleep_summary.py:90`).
- DOCUMENTED: `sleep_summary.py` applies `asyncio.wait_for(..., timeout=MEMORY_LAST_CHAT_TIMEOUT_S)`, default `8.0`, clamped to `1.0..30.0`, around one `client.chat.completions.create(...)` call (`reachy_companion/src/reachy_companion/sleep_summary.py:163`-`reachy_companion/src/reachy_companion/sleep_summary.py:175`).
- DOCUMENTED: There is no retry loop in `sleep_summary.py`; any exception is swallowed and logged as `Sleep summary failed: <type>` (`reachy_companion/src/reachy_companion/sleep_summary.py:188`-`reachy_companion/src/reachy_companion/sleep_summary.py:190`).
- INFERRED: `Sleep summary failed: TimeoutError` means this single summary request exceeded the sleep-summary timeout. From the allowed files alone, the likely cause is an unreachable/slow summary backend or model call, not the stop POST directly.
- INFERRED: Daemon teardown can explain the summary timeout only indirectly, by starting shutdown while the summary's separate client is still trying to reach its backend. The code does not show that the stop-current-app daemon endpoint and the summary backend share the same host/port.

## Finding 4: Fix Proposal

- DOCUMENTED: The current stop-request helper already returns `False` on handled request failure and lets the caller continue to local stop (`reachy_companion/src/reachy_companion/app_lifecycle.py:30`-`reachy_companion/src/reachy_companion/app_lifecycle.py:38`; `reachy_companion/src/reachy_companion/main.py:366`-`reachy_companion/src/reachy_companion/main.py:371`).
- PROPOSED: Change the stop-request catch to include `http.client.HTTPException` and raw `OSError`, for example `except (urllib.error.URLError, http.client.HTTPException, OSError) as e:` after importing `http.client`. `OSError` catches raw `TimeoutError`, `ConnectionResetError`, and `RemoteDisconnected`; `HTTPException` catches `BadStatusLine`/`IncompleteRead` cases that are not `OSError`.
- PROPOSED: Do not let stop-request response failures trigger C6 mic-unmute recovery. At that point the body work has already passed movement-manager stop and usually pose; the app is intentionally entering shutdown, and reopening the microphone contradicts sleep quiesce. The failure should be logged, `stop_current_app_requested=False`, and local stop should still be requested.
- PROPOSED: Keep C6 mic-unmute only for genuinely pre-stop failures that leave the app running before the shutdown signal. Better yet, narrow the outer `try` or add a small inner guard around `request_stop_current_app()` so that daemon response-path failures cannot be classified as "failed before the stop."
- PROPOSED: For sleep summary, prefer adding one bounded retry around `TimeoutError` before moving it earlier. Moving summary into the worker before the stop request would couple shutdown to a backend/model call and can delay app stop by up to the timeout; a retry is a smaller behavioral change and addresses transient backend slowness. If data loss remains unacceptable, persist the transcript before network summarization in a later, non-surgical change.

## Finding 5: Existing Test Coverage

- DOCUMENTED: `tests/` was not present at the workspace root, so grep for `request_stop_current_app`, `go_to_sleep`, and `sleep_summary` found no test files.
- INFERRED: Under the requested `tests/` scope, there is no discoverable coverage pinning the stop-request exception taxonomy, the C6 mic-unmute behavior, the sleep finalizer ordering, or sleep-summary timeout/retry behavior.

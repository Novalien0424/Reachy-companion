# Draft "me too" for pollen-robotics/reachy_mini#1109 (operator posts if desired)

> **Another data point: spurious GPIO23 shutdown on Wireless, daemon
> v1.10.0rc5 (i.e. with PR #505's 200 ms debounce), wall-powered.**
>
> Symptom: exactly one spontaneous **orderly** shutdown (observed folding into
> the sleep pose, then power off — not a freeze or brownout) at the tail of a
> stress-y session: ten back-to-back app stop/start cycles over ~20 minutes,
> each driving the wake choreography plus a return-to-zero head move, speaker
> at 90 %. The robot was on the wall adapter the whole time.
>
> Electricals clean before and after: `vcgencmd get_throttled` = 0x0,
> `rpi_volt` hwmon lcrit alarm 0, no USB/PD errors in dmesg, temps ≤ 53 °C.
> No Wi-Fi involvement (link events clean). The RAM journal was lost with the
> power-off, so we cannot show the gpio daemon's "Shutdown button released"
> line for this event; journald is persistent on this unit now, and we will
> attach it on the next occurrence.
>
> This matches the motor-EMI mechanism described here: v1.10.0rc5 still ships
> the 200 ms debounce from #505, and one event in ~2 days of heavy development
> use suggests the residual false-trigger rate under motor bursts is low but
> real. +1 for a longer `hold_time`-based debounce as proposed.

Context for the operator: our diagnosis session is recorded in `progress.md`
("Flaky-connection + shutdown investigation closed", 2026-08-24). We did NOT
mask `gpio-shutdown-daemon` (it is a daemon service, and masking trades rare
clean shutdowns for routine hard power cuts — the venv-corruption path of
upstream #599).

---
title: Reachy Companion
emoji: 🤖
colorFrom: purple
colorTo: gray
sdk: static
pinned: false
tags:
  - reachy_mini
  - reachy_mini_python_app
---

# Reachy Companion

Forked from the Reachy Mini conversation app.

Use the `src/reachy_companion/profiles/_reachy_companion_locked_profile` folder to customize your own app from this template:
- Edit instructions `_reachy_companion_locked_profile/instructions.txt`
- Edit available tools in `_reachy_companion_locked_profile/tools.txt`
- You can create your own tools in `_reachy_companion_locked_profile` by subclassing the `Tool` class.

Do not forget to customize:
- this `README.md` file
- the `index.html` file (Hugging Face Spaces landing page)
- the `src/reachy_companion/static/index.html` (the web app parameters page)

The original README from the conversation app is available in `README_OLD.md`.
# PRD — Reachy Mini Realtime AI Agent POC

**Version:** 0.2  
**Date:** August 2026  
**Stage:** Proof of Concept  
**Robot:** Reachy Mini Wireless  
**Primary AI Model:** `gpt-realtime-2.1`

---

## 1. Product Goal

Build a custom Reachy Mini application that demonstrates a more capable AI companion experience while preserving the best parts of the official Reachy ecosystem.

The POC should prove that Reachy can:

1. Have fast, natural, interruptible voice conversations.
2. Look at and track the person speaking.
3. React with appropriate physical expressions and movements.
4. Use its camera to understand what it sees.
5. Search the live web when current information is required.
6. Use external tools and MCP integrations.
7. Add new Skills, including controlling devices in the home.

The goal is **not** to build a generic robotics or agent platform yet.

---

# 2. Mandatory Pre-Development Research

Before implementing the POC, the developer must first study the current official Reachy Mini codebase.

### A. Reachy Mini SDK

Repository:

`pollen-robotics/reachy_mini`

Understand how the official SDK handles:

- Robot connection
- Camera
- Microphone and speaker
- Face/head tracking
- Motion
- Robot state
- App lifecycle

The official SDK is the foundation and should be reused instead of recreating robot-level functionality. citeturn238566search1turn238566search6

### B. Reachy Mini Conversation App

Repository:

`pollen-robotics/reachy_mini_conversation_app`

Study especially:

- OpenAI Realtime integration
- Audio handling
- Turn handling
- Face tracking
- Camera usage
- Emotion movements
- Speech-reactive movements
- Movement arbitration
- Tool calling

The current official app already supports multiple realtime backends, vision, layered motion, head tracking, and asynchronous tools. These implementations should be reused or adapted where practical rather than recreated unnecessarily. citeturn238566search0

### Development Principle

Create **our own application repository**, but use the official repositories as reference implementations and dependencies.

Do not permanently develop as a heavily modified fork unless investigation shows that doing so is clearly simpler.

---

# 3. Core Experience

The intended experience is:

> Reachy feels like a physically present AI character rather than a voice assistant attached to a robot.

The interaction should combine:

**Voice + Vision + Physical Reaction + Tools**

---

# 4. User Stories

## US-01 — Natural Conversation

**As a user,**  
I want to speak naturally with Reachy, including short pauses and interruptions,  
**so that** I do not need to adapt my speaking style to the robot.

Requirements:

- Use `gpt-realtime-2.1`.
- User can interrupt Reachy while it is speaking.
- Reachy should avoid prematurely responding during normal mid-sentence pauses.
- Chinese conversation is a primary POC scenario.

`gpt-realtime-2.1` supports realtime audio input/output and configurable reasoning. citeturn778729view0

---

## US-02 — Reachy Looks at Me

**As a user,**  
I want Reachy to naturally look toward me while we interact,  
**so that** the conversation feels physically engaging.

Requirements:

- Use Reachy's existing face/head tracking.
- Tracking should continue without requiring the AI model to repeatedly issue movement commands.
- Head movement should remain smooth and not constantly jitter.

---

## US-03 — Reachy Shows Emotion

**As a user,**  
I want Reachy to physically react to what I say,  
**so that** its responses feel expressive rather than purely verbal.

Example:

> User: "I finally got the job!"

Reachy may:

- React with an excited/happy movement.
- Move its head/body/antennas.
- Respond verbally.

Existing official Conversation App emotions and motion behavior should be used as the primary reference before creating new animations. citeturn238566search0

---

## US-04 — Reachy Can See

**As a user,**  
I want to ask Reachy about something in front of it,  
**so that** the conversation can include the physical environment.

Example:

> "What am I holding?"

Reachy should:

1. Capture an image using its camera.
2. Provide the image to `gpt-realtime-2.1`.
3. Answer based on what it sees.

`gpt-realtime-2.1` supports image input in addition to realtime audio. citeturn778729view0

Continuous cloud video analysis is not required.

---

## US-05 — Reachy Can Search the Web

**As a user,**  
I want Reachy to automatically search the internet when I ask about current information,  
**so that** it is not limited to model knowledge.

Example:

> "What happened with NVIDIA today?"

Reachy should recognize that fresh information is required, invoke a web-search tool, and answer using the result.

The user should not need to explicitly say:

> "Use web search."

---

## US-06 — Reachy Can Use Tools

**As a user,**  
I want Reachy to perform actions rather than only answer questions.

The POC should demonstrate tool calling for at least:

- Camera
- Web search
- Robot expression/action
- One external service

`gpt-realtime-2.1` supports function calling. citeturn778729view1

---

## US-07 — Reachy Can Access an External Service

**As a user,**  
I want Reachy to access an external service such as Notion,  
**so that** it can work with information outside the conversation.

Example:

> "What is the latest status of my Magic Mirror project in Notion?"

For the POC, supporting **one working MCP integration** is sufficient.

The POC should validate the integration rather than attempt to create a universal MCP management platform.

---

## US-08 — Reachy Can Control the Home

**As a user,**  
I want to ask Reachy to control something in the house using natural language.

Examples:

> "Turn on the living room lights."

> "Make the room cooler."

> "Play some music."

The POC only needs **one real Home Control Skill** to demonstrate the concept.

The specific integration may use Home Assistant, MCP, or another practical API.

---

## US-09 — New Skills Can Be Added

**As a developer,**  
I want to add another capability without rewriting the conversation system,  
**so that** Reachy's functionality can grow over time.

Future examples might include:

- Home control
- Notion
- Calendar
- Music
- Weather
- Personal knowledge
- Guest greeting
- Custom APIs

For the POC, this only requires a **simple and understandable extension pattern**.

A full plugin marketplace or generic Skill platform is explicitly out of scope.

---

# 5. Primary User Journey

## Journey A — Normal Conversation

1. Reachy is ready.
2. User approaches and begins talking.
3. Reachy tracks the user's face.
4. User speaks naturally, including brief pauses.
5. Reachy determines when the turn is complete.
6. `gpt-realtime-2.1` responds through speech.
7. Reachy performs subtle speaking movement.
8. If appropriate, Reachy adds an emotional reaction.
9. User interrupts Reachy.
10. Reachy stops speaking and listens immediately.

**Success:** conversation feels fast and does not require unnatural speaking behavior.

---

# 6. Extended User Journeys

## Journey B — Vision

User:

> "What am I holding?"

Reachy:

1. Looks toward the user.
2. Captures a camera image.
3. Interprets the image.
4. Answers verbally.

---

## Journey C — Current Information

User:

> "What is the weather tomorrow?"

or:

> "What is the latest OpenAI news?"

Reachy:

1. Recognizes that current information is required.
2. Invokes web search.
3. May briefly indicate that it is checking.
4. Returns the current answer.
5. Continues the same conversation.

---

## Journey D — External Knowledge

User:

> "Check my Notion and tell me the current project status."

Reachy:

1. Determines that an external tool is required.
2. Calls the configured integration.
3. Receives the result.
4. Summarizes it conversationally.

---

## Journey E — Physical Skill

User:

> "Turn on the living room lights."

Reachy:

1. Understands the requested action.
2. Calls the Home Control Skill.
3. Executes the action.
4. Confirms the result verbally and optionally with a small physical reaction.

---

# 7. POC Functional Scope

The POC must demonstrate:

- `gpt-realtime-2.1` speech-to-speech conversation.
- Good conversational turn handling.
- Barge-in / interruption.
- Face tracking.
- Existing Reachy expression/motion reuse.
- Camera-based visual question answering.
- Live web search.
- Function/tool calling.
- One MCP/external-service integration.
- One Home Control Skill.
- Ability to add another Skill without rewriting the conversational core.

---

# 8. POC Success Criteria

The POC is successful if the following five demonstrations work reliably.

### Demo 1 — Conversation

A user can maintain a natural multi-turn Chinese conversation and interrupt Reachy while it is speaking.

### Demo 2 — Expression

Reachy produces an appropriate physical reaction during conversation.

### Demo 3 — Vision

The user shows Reachy an object and Reachy correctly describes it.

### Demo 4 — Web

The user asks a question requiring information from today and Reachy automatically searches before answering.

### Demo 5 — Skill

The user asks Reachy to control one real device in the home and the action succeeds.

---

# 9. Explicit Non-Goals

The POC should **not** build:

- A generic agent platform.
- A generic plugin marketplace.
- A sophisticated Skill SDK.
- Multi-model routing.
- Cost optimization.
- Long-term memory architecture.
- Complex user identity/profile systems.
- Continuous video understanding.
- Advanced authorization systems.
- A new robot motion engine.
- Custom motor control.
- Custom face recognition.
- A mobile application.
- A new desktop application.
- Production-scale observability.
- Enterprise security architecture.

These should only be considered after the core experience has been proven.

---

# 10. Implementation Philosophy

For every feature, use the following order:

1. **Check whether Reachy Mini SDK already provides it.**
2. **Check how the official Conversation App implements it.**
3. **Reuse the existing implementation if it solves the problem.**
4. Modify it only where the POC requirements are different.
5. Write new functionality only when necessary.

In particular, do not recreate:

- Face tracking.
- Camera access.
- Robot motion primitives.
- Existing emotion animations.
- Existing speech-reactive movements.

The POC should spend engineering effort on what is actually different:

- `gpt-realtime-2.1`
- Better conversation behavior
- Web access
- External tools
- MCP
- Skills
- Home interaction

---

# 11. Adversarial Review

The following potential mistakes must be actively avoided.

### Mistake 1 — Rebuilding the Official App

The official Conversation App already solves many robot-specific problems.

**Decision:** inspect and reuse first.

### Mistake 2 — Turning the POC into a Platform Project

"Skills" can easily lead to months of framework design.

**Decision:** implement one Home Skill and one external integration. Generalize only what is obviously necessary.

### Mistake 3 — Putting Every Robot Movement Through the LLM

This creates latency and unnatural behavior.

**Decision:** basic face tracking and normal robot behavior should continue using existing Reachy capabilities.

### Mistake 4 — Overbuilding MCP

The objective is to prove that Reachy can use external capabilities, not to build an MCP management product.

**Decision:** one reliable MCP integration is enough for the POC.

### Mistake 5 — Continuous Vision

Continuously sending camera frames increases complexity and cost without proving the core concept.

**Decision:** use local tracking plus on-demand camera snapshots.

### Mistake 6 — Optimizing Cost Before Experience

The first question is whether `gpt-realtime-2.1` creates a meaningfully better interaction.

**Decision:** establish the best interaction first. Optimize model cost later.

### Mistake 7 — Designing the Final Architecture Too Early

The official code may already solve problems we would otherwise design abstractions for.

**Decision:** architecture decisions come **after** inspecting the SDK and Conversation App and completing the first integration spike.

---

# 12. Definition of Done

The POC is done when a person can walk up to Reachy and naturally:

1. Talk with it.
2. Pause and interrupt it.
3. Be visually tracked.
4. See appropriate physical reactions.
5. Ask what Reachy sees.
6. Ask a question requiring live web information.
7. Ask it to retrieve something from one external service.
8. Ask it to control one real device in the home.

If those eight experiences work convincingly, the POC has proven the product direction.

Anything beyond that should be justified by what is learned from the POC rather than designed in advance.
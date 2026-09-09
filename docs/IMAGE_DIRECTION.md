# How image direction works

Every scene is directed from four inputs instead of sending raw script text to
an image model.

| Input | What it controls | Example |
|---|---|---|
| Narration | Subject and factual boundary | A closed 1950s neighborhood diner |
| Channel theme | Era, palette, texture, camera language | Warm Kodachrome documentary |
| Emotion | Lighting, framing, atmosphere | Loss → negative space and cooler shadows |
| Narrative role | Visual priority and review attention | Hook, reveal, climax, closing |

The local planner first divides the complete script into the requested number of
scenes. It uses narration word count to assign proportional timestamps across the
voice-over duration. Each segment is tagged for emotion and narrative role. The
final prompt combines the concrete subject, era clues found in the text, emotional
direction, the selected theme, and a clean 16:9 instruction.

Emotion also selects an initial timeline motion:

| Emotion | Visual direction | Motion |
|---|---|---|
| Nostalgia | Warm practical light, intimate memory detail | Slow push |
| Joy | Open composition, brighter natural color | Pan right |
| Loss | Negative space and fading warm light | Slow pull |
| Suspense | Isolated evidence and controlled shadows | Detail push |
| Reveal | Strong contrast and crisp focal hierarchy | Slow push |
| Urgency | Dynamic framing and directional pressure | Pan left |
| Neutral | Balanced observational explanation | Slow push |

The generated prompt, model route, provider, candidate count, motion, transition,
and selected asset remain editable per scene. High-importance scenes are labelled,
but the tool does not automatically spend more money on them.

For the optional Gemini planner, the same `SceneDraft` contract is returned, so
switching planners does not change the database, queue, timeline, or interface.


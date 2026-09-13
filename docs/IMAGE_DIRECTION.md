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

If the requested image count is larger than the number of complete script
sentences, the local and Gemini Text planners return fewer scenes and show a target
warning. They do not split an unfinished sentence merely to manufacture another
visual.

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
| Brief emphasis | Centered object/detail with foreground separation | Pop in |

The generated prompt, model route, provider, candidate count, motion, transition,
and selected asset remain editable per scene. High-importance scenes are labelled,
but the tool does not automatically spend more money on them.

For the optional Gemini planner, the same `SceneDraft` contract is returned, so
switching planners does not change the database, queue, timeline, or interface.

Gemini Precision Sync treats the measured voice-over as the timing authority, but
it does not let the planning model create arbitrary cuts. Word timestamps are
grouped into complete sentences or clearly paused utterances, with punctuation from
the supplied script restored when the transcript omits it. Long planning batches
start and end only between those units. Every returned visual boundary is checked
and snapped to the real pause; a plan that changes imagery during an unfinished
sentence is rejected without replacing the existing scenes.

With **Target images** left blank, automatic pacing becomes the scene-count
authority. It groups complete narration units into main scenes lasting no more than
5 seconds through minute 20, 8 seconds through minute 40, and 10 seconds after
minute 40 whenever sentence boundaries permit. A short emphatic utterance may be
isolated as a maximum two-second pop-in detail. The generated prompt centers its key
item and the timeline applies the matching `pop_in` motion. Entering a target image
count switches back to deliberate fixed-count planning.

# Channel editing styles

One JSON file per channel, written in plain words. The tool reads them from its data folder
(`~/Library/Application Support/LocalVideoStudio/editing_styles/<channel>.json`); these copies are the saved originals.

- `v2_forgotten_flavors.json`: Forgotten Flavors of USA (V2). Based on the channel's most viral video
  (https://www.youtube.com/watch?v=uVYtlsvecP4). Saved on 2 Oct 2026, before the long-video test.

The look that goes with it (orange name labels, lime-yellow Montserrat ExtraBold key captions, no film look,
2-4 s shots, counting number graphics) is in this branch's code: `app/channel_kits.py` (kit `v2`),
`app/name_label.py`, `motion_web/hf/number_fact/`. The previous V2 look is the kit `v2_classic`.

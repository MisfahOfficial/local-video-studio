from __future__ import annotations

from .domain import ThemePreset


THEMES: dict[str, ThemePreset] = {
    "us_nostalgia": ThemePreset(
        id="us_nostalgia",
        name="US Nostalgia",
        description="Authentic mid-century American family, diner and community imagery.",
        visual_style=(
            "authentic American documentary photograph, natural human expressions, practical lived-in spaces, "
            "restrained Kodachrome character, subtle 35mm film grain, historically believable materials"
        ),
        palette="warm cream, faded red, tobacco brown, dusty blue, restrained golden practical light",
        camera_language="eye-level documentary framing, believable 35mm lens, clear foreground subject and layered depth",
        negative_prompt=(
            "modern appliances, smartphones, contemporary packaging, glossy AI look, plastic skin, distorted hands, "
            "extra fingers, duplicated people, unreadable text, logo, watermark, oversaturated colours"
        ),
    ),
    "british_nostalgia": ThemePreset(
        id="british_nostalgia",
        name="British Nostalgia",
        description="Rustic post-war British homes, schools, bakeries and community halls.",
        visual_style=(
            "authentic British social documentary photograph, modest post-war interiors, natural faces, tactile food, "
            "restrained analogue film response, subtle grain, never glossy"
        ),
        palette="muted cream, sage green, oxblood, brown wood, cool window light and warm tungsten pools",
        camera_language="human-height observational camera, 40mm documentary lens, practical composition",
        negative_prompt=(
            "American architecture, modern kitchen, contemporary clothes, luxury styling, glossy advertising, "
            "plastic food, distorted hands, duplicated objects, text, logo, watermark"
        ),
    ),
    "food_documentary": ThemePreset(
        id="food_documentary",
        name="Food Documentary",
        description="Tactile, believable food preparation with strong ingredient detail.",
        visual_style=(
            "realistic editorial food documentary, believable portions, tactile ingredients, natural steam and crumbs, "
            "working kitchen atmosphere, restrained styling"
        ),
        palette="ingredient-led natural colour, warm practical light, neutral shadows",
        camera_language="alternating macro food detail, hands-at-work medium shots and contextual kitchen wides",
        negative_prompt=(
            "plastic food, impossible garnish, excessive shine, luxury restaurant plating, deformed hands, duplicate utensils, "
            "text, logo, watermark"
        ),
        default_motion="detail_push",
    ),
    "history_documentary": ThemePreset(
        id="history_documentary",
        name="History Documentary",
        description="Grounded historical reconstructions with restrained cinematic scale.",
        visual_style=(
            "historically grounded documentary reconstruction, period-correct clothing and architecture, realistic weathering, "
            "natural faces, cinematic but believable light, fine analogue grain"
        ),
        palette="period-appropriate natural colour with restrained contrast",
        camera_language="establishing wides, observational medium shots and evidence-focused close-ups",
        negative_prompt=(
            "modern objects, fantasy armour, anachronistic clothing, theatrical cosplay, glossy CGI, plastic skin, "
            "distorted anatomy, text, logo, watermark"
        ),
    ),
}


def get_theme(theme_id: str) -> ThemePreset:
    return THEMES.get(theme_id, THEMES["us_nostalgia"])


def list_themes() -> list[dict[str, object]]:
    return [theme.to_dict() for theme in THEMES.values()]


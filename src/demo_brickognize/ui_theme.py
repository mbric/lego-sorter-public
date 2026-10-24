"""Gradio UI theme switching (persist + live CSS injection)."""

from __future__ import annotations

import json

import gradio as gr

from orchestrator.sort_cycle import REFERENCE_PATH

# Gradio fixes the theme at launch, but every theme is just a set of CSS
# variables on :root — injecting a later <style> block re-skins the app live.
THEME_FACTORIES = {
    "Soft": gr.themes.Soft,
    "Default": gr.themes.Default,
    "Ocean": gr.themes.Ocean,
    "Monochrome": gr.themes.Monochrome,
    "Citrus": gr.themes.Citrus,
    "Ember": gr.themes.Ember,
    "Glass": gr.themes.Glass,
    "Origin": gr.themes.Origin,
    "Base": gr.themes.Base,
    "Neon": gr.themes.Neon,
    "Cyberpunk": gr.themes.Cyberpunk,
}
DEFAULT_THEME_NAME = "Default"
_THEME_CSS_CACHE: dict[str, str] = {}


def _theme_config_path():
    return REFERENCE_PATH.parent / "ui_theme.json"


def load_ui_theme_name() -> str:
    path = _theme_config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        name = raw.get("theme", DEFAULT_THEME_NAME)
        if name in THEME_FACTORIES:
            return name
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_THEME_NAME


def _save_ui_theme_name(name: str) -> None:
    path = _theme_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"theme": name}) + "\n", encoding="utf-8")
    except OSError:
        pass  # theme still applies for this session


def _theme_style_block(name: str) -> str:
    if name not in THEME_FACTORIES:
        name = DEFAULT_THEME_NAME
    if name not in _THEME_CSS_CACHE:
        _THEME_CSS_CACHE[name] = THEME_FACTORIES[name]()._get_theme_css()
    return f"<style>{_THEME_CSS_CACHE[name]}</style>"


def apply_ui_theme(name: str) -> str:
    """Dropdown handler: persist choice and inject the theme's CSS live."""
    _save_ui_theme_name(name)
    return _theme_style_block(name)

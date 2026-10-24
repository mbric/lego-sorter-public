"""Gradio UI for teaching ReBeL home / rest / bin dump poses."""

from __future__ import annotations

import logging

import gradio as gr

from .poses import MVP_BIN_IDS, POSES_PATH
from .teach_session import TeachSession, TeachStatus

logger = logging.getLogger(__name__)

SESSION = TeachSession()


def _pack(st: TeachStatus) -> tuple[str, str, str]:
    conn = "Connected" if st.connected else "Disconnected"
    return st.message, f"{conn}\n{st.live}", st.summary


def _refresh() -> tuple[str, str, str]:
    return _pack(SESSION.status())


def mount_teach_ui(demo: gr.Blocks, *, heading: bool = True) -> list:
    """Build teach controls in the current Gradio context (e.g. inside a Tab).

    Returns the status output components. Registers a ``demo.load`` refresh.
    Does not auto-connect — operator must click Connect (CRI is one client).
    """
    if heading:
        gr.Markdown(
            f"""
### ReBeL pose teacher

Nudge with the buttons, then **Save** when it looks right.

- **home** — scoop under the trapdoor (working catch)
- **rest** — idle park clear of furniture (use **Park** when done)
- **bin approach / dump** — clear pose, then pour tilt

Poses: `{POSES_PATH}` · Close Windows **iRC** before Connect · Keep E-stop handy
"""
        )

    with gr.Row():
        connect_btn = gr.Button("Connect", variant="primary")
        stop_btn = gr.Button("Stop", variant="stop")
        park_btn = gr.Button("Park + disable", variant="stop")
        disconnect_btn = gr.Button("Disconnect (no park)")
        refresh_btn = gr.Button("Refresh status")

    message = gr.Textbox(label="Message", interactive=False, lines=2)
    live = gr.Textbox(label="Live pose", interactive=False, lines=3)
    summary = gr.Textbox(label="Saved poses", interactive=False, lines=8)
    outs = [message, live, summary]

    with gr.Row():
        override = gr.Slider(5, 50, value=20, step=1, label="Override %")
        vel = gr.Slider(5, 50, value=20, step=1, label="Goto / test velocity %")
        cart_step = gr.Slider(1, 50, value=10, step=1, label="Cart nudge mm")
        joint_step = gr.Slider(0.5, 15, value=3, step=0.5, label="Joint nudge °")

    with gr.Accordion("Cartesian nudge (base XYZ)", open=True):
        gr.Markdown("Move the scoop in space. Use small steps near furniture.")
        cart_buttons: dict[str, gr.Button] = {}
        with gr.Row():
            for axis, label in (("x", "X"), ("y", "Y"), ("z", "Z")):
                with gr.Column():
                    gr.Markdown(f"**{label}**")
                    with gr.Row():
                        cart_buttons[f"{axis}-"] = gr.Button(f"{label} −")
                        cart_buttons[f"{axis}+"] = gr.Button(f"{label} +")

    with gr.Accordion("Joint nudge (wrist A5/A6 for dump tilt)", open=True):
        joint_buttons: dict[str, gr.Button] = {}
        with gr.Row():
            for axis in ("a1", "a2", "a3", "a4", "a5", "a6"):
                with gr.Column():
                    gr.Markdown(f"**{axis.upper()}**")
                    with gr.Row():
                        joint_buttons[f"{axis}-"] = gr.Button("−")
                        joint_buttons[f"{axis}+"] = gr.Button("+")

    with gr.Accordion("Save / goto named poses", open=True):
        with gr.Row():
            save_home = gr.Button("Save home")
            save_rest = gr.Button("Save rest")
            goto_home = gr.Button("Goto home")
            goto_rest = gr.Button("Goto rest")

        with gr.Row():
            bin_id = gr.Dropdown(
                choices=list(MVP_BIN_IDS),
                value="red",
                label="Bin",
                allow_custom_value=True,
            )
            save_approach = gr.Button("Save approach")
            save_dump = gr.Button("Save dump")
            goto_approach = gr.Button("Goto approach")
            goto_dump = gr.Button("Goto dump")

        with gr.Row():
            test_dump = gr.Button("Test dump cycle", variant="primary")
            reload_btn = gr.Button("Reload poses.json")

    connect_btn.click(lambda: _pack(SESSION.connect()), outputs=outs)
    stop_btn.click(lambda: _pack(SESSION.stop()), outputs=outs)
    park_btn.click(lambda: _pack(SESSION.park()), outputs=outs)
    disconnect_btn.click(
        lambda: _pack(SESSION.disconnect(park=False)), outputs=outs
    )
    refresh_btn.click(_refresh, outputs=outs)
    reload_btn.click(lambda: _pack(SESSION.reload_poses()), outputs=outs)

    override.release(
        lambda v: _pack(SESSION.set_override(v)),
        inputs=[override],
        outputs=outs,
    )
    vel.release(
        lambda v: _pack(SESSION.set_velocity(v)),
        inputs=[vel],
        outputs=outs,
    )

    def _apply_steps(c_mm: float, j_deg: float):
        return _pack(SESSION.set_nudge_steps(c_mm, j_deg))

    cart_step.release(_apply_steps, inputs=[cart_step, joint_step], outputs=outs)
    joint_step.release(_apply_steps, inputs=[cart_step, joint_step], outputs=outs)

    for axis in ("x", "y", "z"):
        cart_buttons[f"{axis}-"].click(
            lambda a=axis: _pack(SESSION.nudge_cart(a, -1.0)),
            outputs=outs,
        )
        cart_buttons[f"{axis}+"].click(
            lambda a=axis: _pack(SESSION.nudge_cart(a, 1.0)),
            outputs=outs,
        )

    for axis in ("a1", "a2", "a3", "a4", "a5", "a6"):
        joint_buttons[f"{axis}-"].click(
            lambda a=axis: _pack(SESSION.nudge_joint(a, -1.0)),
            outputs=outs,
        )
        joint_buttons[f"{axis}+"].click(
            lambda a=axis: _pack(SESSION.nudge_joint(a, 1.0)),
            outputs=outs,
        )

    save_home.click(lambda: _pack(SESSION.save_named("home")), outputs=outs)
    save_rest.click(lambda: _pack(SESSION.save_named("rest")), outputs=outs)
    goto_home.click(lambda: _pack(SESSION.goto_named("home")), outputs=outs)
    goto_rest.click(lambda: _pack(SESSION.goto_named("rest")), outputs=outs)

    save_approach.click(
        lambda b: _pack(SESSION.save_bin(b, "approach")),
        inputs=[bin_id],
        outputs=outs,
    )
    save_dump.click(
        lambda b: _pack(SESSION.save_bin(b, "dump")),
        inputs=[bin_id],
        outputs=outs,
    )
    goto_approach.click(
        lambda b: _pack(SESSION.goto_bin(b, "approach")),
        inputs=[bin_id],
        outputs=outs,
    )
    goto_dump.click(
        lambda b: _pack(SESSION.goto_bin(b, "dump")),
        inputs=[bin_id],
        outputs=outs,
    )
    test_dump.click(
        lambda b: _pack(SESSION.test_dump(b)),
        inputs=[bin_id],
        outputs=outs,
    )

    demo.load(_refresh, outputs=outs, show_progress="hidden")
    return outs


def build_app() -> gr.Blocks:
    """Standalone pose-teacher window (also available as a tab in the main demo)."""
    with gr.Blocks(title="ReBeL pose teacher") as demo:
        gr.Markdown("# ReBeL pose teacher")
        mount_teach_ui(demo, heading=True)
    return demo

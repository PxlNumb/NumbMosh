# NumbMosh

![image alt](https://github.com/PxlNumb/NumbMosh/blob/1fcddf7e683afaf178d10f1d51d1e52d7349a4d9/GUI%20v0.1.0.png)

# Datamosh for DaVinci Resolve

Real bitstream datamoshing applied **in place** to the clip on your timeline.
Inspired by Datamosher Pro (Akascape, MIT), tomato.py, pymosh and FFglitch. Independent re-implementation; no FFglitch code, none of the paid version's effects.

## Install (Windows)
1. Extract the folder and double-click `install.bat`.
2. Restart DaVinci Resolve.
3. Put the playhead over a clip, then `Workspace > Scripts > Datamosh`.

Uninstall: `install.bat -Uninstall`. macOS/Linux: `./install.sh` (untested in Resolve).

## How it works
Datamosh edits the compressed video stream, so a rendered file is unavoidable. The plugin renders it
to a cache folder and then points the existing media pool clip at it (`ReplaceClip`).
Your timeline clip stays the same clip: same cuts, same position, same effects. No new clip, no Loader.
The original file is never changed. **Restore original** points the clip back to it.
Applying again always starts from the original, so effects never stack by accident.

## Controls
| Control | Use |
|---|---|
| Effect | Void, Classic, Bloom, Repeat (only the relevant controls are shown) |
| Start frame | First frame of the effect, counted from the start of the source file. **Playhead** fills it in from the playhead |
| Length | Classic: frames in the range. Repeat: frames in the repeated block |
| Repeats | Bloom, Repeat: number of repetitions |
| Sensitivity | Void: scene-cut detection |
| ProRes | ProRes (.mov) instead of H.264 (.mp4) |

## Effects
- **Void**: removes I-frames at detected scene cuts.
- **Classic**: removes I-frames from Start frame to Start frame + Length.
- **Bloom**: removes the I-frame at Start frame, then repeats the next P-frame.
- **Repeat**: repeats a block of frames.

The result keeps the original frame count, so the clip's length on the timeline does not change.

## Requirements and limits
- DaVinci Resolve 18.5 or newer with scripting available (Studio is the safest bet).
- Video files only (no image sequences). Audio is not touched by the render.
- Resolve may freeze briefly while rendering.
- Tested outside Resolve with a simulated Resolve/UI; not yet tested inside Resolve itself.

## Command line
    python datamosh_core.py in.mp4 out.mp4 --effect classic --start 30 --end 90 --keep-length

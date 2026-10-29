# Datamosh for Fusion

Real bitstream datamoshing as a Fusion macro node for DaVinci Resolve, with audio that follows the edit.
Inspired by Datamosher Pro (Akascape, MIT), tomato.py, pymosh and FFglitch. Independent re-implementation; includes no FFglitch code.

## Install (Windows)
1. Extract the folder, double-click `install.bat`.
2. Restart DaVinci Resolve.
3. Fusion page: Effects Library > Macros > Datamosh. If it is not listed, drag `Datamosh.setting` (from `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Macros`) into the node graph.

Uninstall: `install.bat -Uninstall`. macOS/Linux: `./install.sh` (untested in Resolve).

## Controls
| Control | What it does |
|---|---|
| Video File (empty = automatic) | Leave empty: uses the MediaIn/Loader of the comp, or the clip under the playhead |
| Mosh Type | Auto Melt, Melt at Frame, Smear / Bloom, Stutter (see below) |
| Effect Start Frame | Frame where the effect happens |
| Melt Length | Auto Melt / Melt at Frame: how many frames the picture stays melted before it recovers (0 = until the end) |
| Stutter Block Length | Stutter: how many frames are repeated |
| Repeat Count | Smear / Stutter: number of repetitions |
| Scene Cut Sensitivity | Auto Melt: lower value finds more cuts |
| Fake Cut Jump | Skips N frames at the start frame to create a cut inside a single clip (0 = off) |
| Audio | No audio / Original (synced) / Pitch shift / Glitch / Pitch shift + glitch |
| Pitch Shift (semitones) | -12 .. +12, used by the pitch modes |
| Output Format | H.264 (.mp4) or ProRes (.mov) |
| RENDER DATAMOSH | Opens a console with the log, renders next to the source file and loads the result |

## Mosh types
- **Auto Melt (at scene cuts)**: removes the keyframe at every scene cut, so each cut melts into the next shot. If the clip has no cuts, a fake cut is made at the Start Frame.

https://github.com/user-attachments/assets/ff936233-304e-40d3-b0a0-9d62570736b5

- **Melt at Frame (remove keyframe)**: makes a cut at the Start Frame (Fake Cut Jump) and removes its keyframe. The old picture melts under the motion of the new content for Melt Length frames.

*not working*

- **Smear / Bloom (repeat motion)**: removes a keyframe and repeats the next P-frame, so the motion smears and blooms.

https://github.com/user-attachments/assets/66c2fd4e-8a78-4f3e-8ad1-e8c20e15147c

- **Stutter (repeat frames)**: repeats a block of frames.

https://github.com/user-attachments/assets/22786283-23af-45eb-a5f4-1329bc6bf2c3

Why the fake cut: a datamosh needs a cut. With one clip and no cut, removing a keyframe changes almost nothing. The jump creates the cut. The result is shorter by that many frames, and the audio is cut the same way.

## Audio
- **Original (synced)**: the sound follows the video edit (jump cut, repeated block, repeated frames).
- **Pitch shift**: the moshed part is pitch shifted (keeps its length).
- **Glitch / bug**: the moshed part gets random stutter loops, reversed bits, dropouts and a bitcrusher.
- **Pitch shift + glitch**: both.
The effect only touches the moshed part; the rest of the sound is untouched. A clip without an audio track gives a silent result.

## Log
The render console shows what happened (removed keyframes, repeated frames, audio regions, frame counts) and stays open until you press a key. The same text is saved in `last_run.log` in the install folder (`%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\DatamoshFusion`).

## Fallback script
`Workspace > Scripts > Datamosher` runs the same engine from a dialog.

## Command line
    python datamosh_core.py in.mp4 out.mp4 --effect void --audio glitch
    python datamosh_core.py in.mp4 out.mp4 --effect classic --start 100 --jump 60 --melt-length 60 --audio pitch --pitch -7
    python datamosh_core.py in.mp4 out.mp4 --effect bloom --start 72 --count 30
    python datamosh_core.py in.mp4 out.mp4 --effect repeat --start 20 --block-length 6 --count 8 --audio pitch_glitch

## Known limits
- Renders the whole source file (ignores Loader trims). Video files only.
- Resolve is blocked while the console window is open.
- Tested outside Resolve with a simulated Fusion; not yet in Resolve itself.

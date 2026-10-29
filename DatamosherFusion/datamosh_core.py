#!/usr/bin/env python3
"""
Datamosher for Fusion - core engine

Real bitstream datamoshing: the video is encoded to raw MPEG-4 Part 2 (no B-frames),
the stream is edited at the VOP (frame) level, then decoded again. Removing an I-frame
makes the following P-frames apply their motion vectors to the previous picture, which
is the classic datamosh look.

Mosh types:
  void     Auto Melt      removes the I-frame at every scene cut
  classic  Melt at Frame  removes the I-frame at a chosen frame
  bloom    Smear          removes an I-frame and repeats the next P-frame (motion smear)
  repeat   Stutter        repeats a block of frames

A single clip has no scene cuts, so there is nothing to melt. "Fake cut" (--jump) skips
N frames at the chosen frame, which creates a hard cut inside the clip. The I-frame of
that cut is then removed, and the picture melts into the new content.

Audio (--audio) is edited together with the video: the same cut / repeat is applied to
the sound, and the moshed part can be pitch shifted and/or glitched.

Requires: Python 3.8+ (stdlib only) and an ffmpeg executable
(PATH, or env var DATAMOSH_FFMPEG, or the pip package imageio-ffmpeg).
"""
import argparse
import array
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave

VERSION = "0.4.0"
START_CODE = b"\x00\x00\x01"
I_FRAME, P_FRAME, B_FRAME = 0, 1, 2
SR, CH = 48000, 2
BPF = CH * 2            # bytes per audio sample frame
INF = 10 ** 9


# --------------------------------------------------------------------- logging
LOGFILE = None


def log(*parts):
    """Print to the console and append to the log file."""
    text = " ".join(str(p) for p in parts)
    try:
        print(text, flush=True)
    except Exception:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)
    if LOGFILE:
        try:
            with open(LOGFILE, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except Exception:
            pass


# ----------------------------------------------------------------- ffmpeg utils
def find_ffmpeg():
    env = os.environ.get("DATAMOSH_FFMPEG")
    if env and os.path.isfile(env):
        return env
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def run(cmd, capture=False):
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        tail = res.stderr.decode("utf-8", "replace")[-1500:]
        raise RuntimeError("ffmpeg failed:\n" + tail)
    return res.stderr.decode("utf-8", "replace") if capture else None


def probe(ffmpeg, src):
    """-> (fps, duration_seconds or None, has_audio)"""
    res = subprocess.run([ffmpeg, "-hide_banner", "-i", src],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    text = res.stderr.decode("utf-8", "replace")
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", text) or re.search(r"(\d+(?:\.\d+)?)\s*tbr", text)
    fps = float(m.group(1)) if m else 24.0
    d = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else None
    return fps, dur, " Audio:" in text


NTSC = {23.976: "24000/1001", 29.97: "30000/1001", 47.952: "48000/1001",
        59.94: "60000/1001", 119.88: "120000/1001"}


def fps_string(fps):
    """Exact frame rate as ffmpeg expects it (29.97 -> 30000/1001).
    A rounded rate makes forced I-frames land on the wrong frame."""
    for approx, exact in NTSC.items():
        if abs(fps - approx) < 0.01:
            return exact
    return ("%.6f" % fps).rstrip("0").rstrip(".")


def fps_value(fps_str):
    if "/" in fps_str:
        a, b = fps_str.split("/")
        return float(a) / float(b)
    return float(fps_str)


def detect_scene_cuts(ffmpeg, src, fps, threshold):
    """Frame numbers where a scene change is detected."""
    cmd = [ffmpeg, "-hide_banner", "-i", src, "-an",
           "-vf", "fps=%s,select='gt(scene,%s)',showinfo" % (fps, threshold),
           "-f", "null", "-"]
    text = run(cmd, capture=True)
    v = fps_value(fps)
    frames = [int(round(float(m.group(1)) * v))
              for m in re.finditer(r"pts_time:\s*([0-9.]+)", text)]
    return sorted(set(f for f in frames if f > 0))


# ----------------------------------------------------------------- m4v parsing
class Frame:
    __slots__ = ("data", "ftype")

    def __init__(self, data, ftype):
        self.data = data
        self.ftype = ftype


def parse_m4v(buf):
    """Split a raw MPEG-4 Part 2 elementary stream into frames.
    Header start codes that precede a VOP (VOS/VO/VOL/GOP/user data) belong to that frame."""
    codes = []
    i = buf.find(START_CODE)
    while i != -1 and i + 3 < len(buf):
        codes.append((i, buf[i + 3]))
        i = buf.find(START_CODE, i + 3)

    frames = []
    cur_start = None
    cur_type = None
    seen_vop = False
    for pos, code in codes:
        if code == 0xB6:  # VOP
            if cur_start is None:
                cur_start = pos
            if seen_vop:
                frames.append(Frame(buf[cur_start:pos], cur_type))
                cur_start = pos
            cur_type = (buf[pos + 4] >> 6) & 0x3
            seen_vop = True
        else:
            if code == 0xB1:  # sequence end
                continue
            if seen_vop:
                frames.append(Frame(buf[cur_start:pos], cur_type))
                cur_start, cur_type, seen_vop = pos, None, False
            elif cur_start is None:
                cur_start = pos
    if seen_vop and cur_start is not None:
        frames.append(Frame(buf[cur_start:len(buf)], cur_type))
    return frames


# ------------------------------------------------------------- video effects
def nearest_iframe(frames, target, tol=3):
    """Index of the I-frame closest to `target` (never frame 0), or None."""
    best = None
    for i, f in enumerate(frames):
        if i > 0 and f.ftype == I_FRAME and abs(i - target) <= tol:
            if best is None or abs(i - target) < abs(best - target):
                best = i
    return best


def fx_void(frames, recover, **_):
    """Auto Melt: remove every I-frame except the first one and the recovery frames."""
    out = []
    for i, f in enumerate(frames):
        if i > 0 and f.ftype == I_FRAME and i not in recover:
            log("  - removed I-frame at frame %d (cut -> melt)" % i)
            continue
        out.append(f)
    return out


def fx_classic(frames, start, melt_length, recover, **_):
    """Melt at Frame: remove the I-frames from `start` for `melt_length` frames (0 = to the end)."""
    last = start + melt_length - 1 if melt_length > 0 else INF
    out = []
    for i, f in enumerate(frames):
        if i > 0 and f.ftype == I_FRAME and start - 1 <= i <= last and i not in recover:
            log("  - removed I-frame at frame %d (melt starts)" % i)
            continue
        out.append(f)
    return out


def fx_bloom(frames, start, count, **_):
    """Smear: remove the I-frame at `start`, then repeat the next P-frame `count` times."""
    out = []
    dropped = False
    target = nearest_iframe(frames, start)
    for i, f in enumerate(frames):
        if i == target:
            dropped = True
            log("  - removed I-frame at frame %d" % i)
            continue
        out.append(f)
        if dropped and f.ftype == P_FRAME:
            out.extend(Frame(f.data, f.ftype) for _ in range(count))
            log("  - repeated the P-frame at frame %d x%d (smear)" % (i, count))
            dropped = False
    return out


def fx_repeat(frames, start, block_length, count, **_):
    """Stutter: repeat the frames [start, start+block_length) `count` extra times."""
    block_length = max(1, block_length)
    start = min(max(0, start), len(frames) - 1)
    seg = frames[start:start + block_length]
    if not seg:
        return frames
    log("  - repeated frames %d..%d x%d (%d new frames)"
        % (start, start + len(seg) - 1, count, len(seg) * count))
    out = frames[:start + block_length]
    for _ in range(count):
        out.extend(Frame(f.data, f.ftype) for f in seg)
    out.extend(frames[start + block_length:])
    return out


def recoveries(cuts, melt_length):
    """Frames where a normal I-frame is kept so the picture recovers after a melt."""
    rec = set()
    if melt_length <= 0:
        return rec
    cuts = sorted(cuts)
    for idx, c in enumerate(cuts):
        r = c + melt_length
        nxt = cuts[idx + 1] if idx + 1 < len(cuts) else None
        if nxt is None or r < nxt:
            rec.add(r)
    return rec


# ---------------------------------------------------------------------- audio
def read_wav(path):
    with wave.open(path, "rb") as w:
        return w.readframes(w.getnframes())


def write_wav(path, pcm):
    with wave.open(path, "wb") as w:
        w.setnchannels(CH)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)


def fit(pcm, nbytes):
    nbytes -= nbytes % BPF
    if len(pcm) >= nbytes:
        return pcm[:nbytes]
    return pcm + b"\x00" * (nbytes - len(pcm))


def declick(b, n=48):
    """1 ms fade in/out on a piece so joins do not click."""
    if len(b) < n * BPF * 4:
        return b
    a = array.array("h")
    a.frombytes(b)
    big = sys.byteorder == "big"
    if big:
        a.byteswap()
    for k in range(n):
        g = k / float(n)
        for c in range(CH):
            a[k * CH + c] = int(a[k * CH + c] * g)
            j = len(a) - 1 - k * CH - (CH - 1 - c)
            a[j] = int(a[j] * g)
    if big:
        a.byteswap()
    return a.tobytes()


def ffmpeg_filter_pcm(ffmpeg, tmp, pcm, af, tag):
    inp = os.path.join(tmp, tag + "_in.wav")
    outp = os.path.join(tmp, tag + "_out.wav")
    write_wav(inp, pcm)
    run([ffmpeg, "-y", "-hide_banner", "-i", inp, "-af", af,
         "-ar", str(SR), "-ac", str(CH), "-c:a", "pcm_s16le", outp])
    return fit(read_wav(outp), len(pcm))


def pitch_shift(ffmpeg, tmp, pcm, semitones, tag):
    f = 2.0 ** (semitones / 12.0)
    af = "asetrate=%d,aresample=%d,atempo=%.6f" % (int(round(SR * f)), SR, 1.0 / f)
    return ffmpeg_filter_pcm(ffmpeg, tmp, pcm, af, tag)


def glitch(ffmpeg, tmp, pcm, tag, seed=1234):
    """Bug effect: random stutter loops, reversed bits, dropouts, then bitcrush."""
    rng = random.Random(seed)
    total = len(pcm)
    if total < BPF * 2000:
        return pcm
    out = bytearray()
    pos = 0
    while len(out) < total:
        n = int(SR * rng.randint(30, 140) / 1000.0) * BPF
        chunk = pcm[pos:pos + n]
        if len(chunk) < BPF * 100:
            pos = 0
            continue
        r = rng.random()
        if r < 0.12:
            chunk = b"\x00" * len(chunk)
        elif r < 0.24:
            a = array.array("i")
            a.frombytes(chunk[:len(chunk) - len(chunk) % 4])
            a.reverse()
            chunk = a.tobytes()
        out += chunk * rng.choice([1, 2, 3, 4])
        pos += n if rng.random() < 0.7 else 0
    out = bytes(out[:total])
    return ffmpeg_filter_pcm(ffmpeg, tmp, out, "acrusher=bits=6:mode=log:aa=1:mix=0.85", tag)


def make_audio_plan(effect, a, fake, cuts, bloom_target, n_frames):
    """List of (src_frame_a, src_frame_b, moshed) segments that make the sound follow the video.
    Frame numbers are source-video frames; moshed=True marks the part that gets audio effects."""
    F, T = False, True
    if fake:                                   # jump cut + melt
        K, J = fake
        s0, L = K + J, a["melt_length"]
        if L > 0:
            return [(0, K, F), (s0, s0 + L, T), (s0 + L, INF, F)]
        return [(0, K, F), (s0, INF, T)]
    if effect == "void":                       # real scene cuts
        segs, cursor = [], 0
        cuts = sorted(cuts)
        for idx, c in enumerate(cuts):
            nxt = cuts[idx + 1] if idx + 1 < len(cuts) else INF
            e = min(c + a["melt_length"], nxt) if a["melt_length"] > 0 else nxt
            if c > cursor:
                segs.append((cursor, c, F))
            segs.append((c, e, T))
            cursor = e
        segs.append((cursor, INF, F))
        return segs
    if effect == "classic":                    # melt without a fake cut
        s, L = a["start"], a["melt_length"]
        if L > 0:
            return [(0, s, F), (s, s + L, T), (s + L, INF, F)]
        return [(0, s, F), (s, INF, T)]
    if effect == "bloom":
        t = bloom_target
        if t is None:
            return [(0, INF, F)]
        segs = [(0, t, F)]
        rem, c = a["count"] + 1, 3
        while rem > 0:
            take = min(c, rem)
            segs.append((t + 1, t + 1 + take, T))
            rem -= take
        segs.append((t + 2, INF, F))
        return segs
    # repeat
    s = min(max(0, a["start"]), max(0, n_frames - 1))
    l = max(1, min(a["block_length"], n_frames - s))
    return [(0, s + l, F)] + [(s, s + l, T)] * a["count"] + [(s + l, INF, F)]


def process_audio(ffmpeg, tmp, src, plan, fpsv, mode, semitones, out_frames):
    raw = os.path.join(tmp, "a_src.wav")
    run([ffmpeg, "-y", "-hide_banner", "-i", src, "-vn", "-map", "0:a:0",
         "-ac", str(CH), "-ar", str(SR), "-c:a", "pcm_s16le", raw])
    pcm = read_wav(raw)

    def fb(frame):
        return int(round(frame * SR / fpsv)) * BPF

    pieces = []
    for a, b, fx in plan:
        s0, s1 = min(len(pcm), fb(a)), min(len(pcm), fb(b))
        if s1 > s0:
            piece = declick(pcm[s0:s1])
            if pieces and pieces[-1][1] == fx:
                pieces[-1][0] += piece
            else:
                pieces.append([piece, fx])

    do_pitch = mode in ("pitch", "pitch_glitch")
    do_glitch = mode in ("glitch", "pitch_glitch")
    out = bytearray()
    for n, (piece, fx) in enumerate(pieces):
        if fx and (do_pitch or do_glitch):
            t0 = len(out) / float(BPF * SR)
            t1 = t0 + len(piece) / float(BPF * SR)
            if do_pitch:
                piece = pitch_shift(ffmpeg, tmp, piece, semitones, "p%d" % n)
                log("  - audio: pitch shift %+g semitones, %.2fs .. %.2fs" % (semitones, t0, t1))
            if do_glitch:
                piece = glitch(ffmpeg, tmp, piece, "g%d" % n, seed=1234 + n)
                log("  - audio: glitch / bitcrush, %.2fs .. %.2fs" % (t0, t1))
        out += piece
    want = int(round(out_frames * SR / fpsv)) * BPF
    final = fit(bytes(out), want)
    path = os.path.join(tmp, "audio_out.wav")
    write_wav(path, final)
    log("  - audio: %.2fs (video %.2fs)" % (len(final) / float(BPF * SR), out_frames / fpsv))
    return path


# ------------------------------------------------------------------- output
def decode_and_mux(ffmpeg, mosh_path, fps, audio_path, out, codec):
    base = [ffmpeg, "-y", "-hide_banner", "-err_detect", "ignore_err",
            "-f", "m4v", "-framerate", fps, "-i", mosh_path]
    if audio_path:
        base += ["-i", audio_path]
    base += ["-vf", "setpts=N/(%s*TB)" % fps, "-r", fps]
    if audio_path:
        base += ["-map", "0:v:0", "-map", "1:a:0"]
    if codec == "prores":
        run(base + ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
            + (["-c:a", "pcm_s16le"] if audio_path else []) + [out])
        return
    aopts = ["-c:a", "aac", "-b:a", "192k"] if audio_path else []
    try:
        run(base + ["-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p"] + aopts + [out])
    except RuntimeError:  # ffmpeg build without libx264
        run(base + ["-c:v", "mpeg4", "-q:v", "2"] + aopts + [out])


# ----------------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(description="Datamosher for Fusion - core engine")
    p.add_argument("input", nargs="?")
    p.add_argument("output", nargs="?")
    p.add_argument("--effect", choices=["void", "classic", "bloom", "repeat"], default="void")
    p.add_argument("--start", type=int, default=30, help="frame where the effect starts")
    p.add_argument("--melt-length", type=int, default=60,
                   help="void/classic: frames the melt lasts before the picture recovers (0 = until the end)")
    p.add_argument("--block-length", type=int, default=8, help="repeat: frames in the repeated block")
    p.add_argument("--count", type=int, default=20, help="bloom/repeat: number of repetitions")
    p.add_argument("--scene", type=float, default=0.30,
                   help="void: scene-cut threshold 0-1 (lower finds more cuts)")
    p.add_argument("--jump", type=int, default=30,
                   help="void/classic: fake cut, skip N frames at the start frame (0 = off)")
    p.add_argument("--audio", choices=["none", "original", "pitch", "glitch", "pitch_glitch"],
                   default="original", help="none = silent, original = synced to the edit, "
                   "pitch / glitch / pitch_glitch = effect on the moshed part")
    p.add_argument("--pitch", type=float, default=-5.0, help="pitch shift in semitones")
    p.add_argument("--fps", type=float, default=0.0, help="override frame rate")
    p.add_argument("--quality", type=int, default=3, help="mpeg4 intermediate qscale (1 best - 31)")
    p.add_argument("--codec", choices=["h264", "prores"], default="h264")
    p.add_argument("--log", default="", help="also write the log to this file")
    p.add_argument("--check", action="store_true", help="print ffmpeg location and exit")
    p.add_argument("--version", action="store_true")
    return p


EFFECT_NAMES = {"void": "AUTO MELT (at scene cuts)", "classic": "MELT AT FRAME",
                "bloom": "SMEAR / BLOOM", "repeat": "STUTTER (repeat frames)"}


def main(argv=None):
    args = build_parser().parse_args(argv)
    global LOGFILE
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    if args.log:
        LOGFILE = args.log
        open(LOGFILE, "w", encoding="utf-8").close()
    if args.version:
        log("datamosh_core", VERSION)
        return 0
    ffmpeg = find_ffmpeg()
    if args.check:
        log(ffmpeg or "FFMPEG_NOT_FOUND")
        return 0 if ffmpeg else 2
    if not ffmpeg:
        log("ERROR: ffmpeg not found. Install it (winget install Gyan.FFmpeg) and restart DaVinci Resolve.")
        return 2
    if not args.input or not args.output:
        log("ERROR: input and output paths are required.")
        return 1
    if not os.path.isfile(args.input):
        log("ERROR: input file does not exist:", args.input)
        return 1

    t0 = time.time()
    fps_f, duration, has_audio = probe(ffmpeg, args.input)
    if args.fps:
        fps_f = args.fps
    fps = fps_string(fps_f)
    fpsv = fps_value(fps)
    n_est = int(round(duration * fpsv)) if duration else None
    effect = args.effect
    start = max(1, args.start)

    log("=" * 60)
    log("DATAMOSH  v%s" % VERSION)
    log("=" * 60)
    log("Source :", args.input)
    log("Output :", args.output)
    log("Effect :", EFFECT_NAMES[effect])
    if effect == "void":
        log("Params : cut sensitivity=%.2f  melt length=%d  fake-cut jump=%d (fallback at frame %d)"
            % (args.scene, args.melt_length, args.jump, start))
    elif effect == "classic":
        log("Params : start=%d  melt length=%d  fake-cut jump=%d" % (start, args.melt_length, args.jump))
    elif effect == "bloom":
        log("Params : start=%d  repeats=%d" % (start, args.count))
    else:
        log("Params : start=%d  block=%d  repeats=%d" % (start, args.block_length, args.count))
    log("Audio  : %s%s" % (args.audio, "" if has_audio or args.audio == "none"
                           else "  (this file has NO audio track)"))
    log("Codec  :", args.codec)
    log("ffmpeg :", ffmpeg)
    log("fps    : %s  |  duration: %s" % (fps, "%.2fs (~%d frames)" % (duration, n_est) if duration else "?"))
    log("")

    tmp = tempfile.mkdtemp(prefix="datamosh_")
    try:
        # ---- decide where the cuts / I-frames go
        fake = None
        cuts = []
        melt = args.melt_length

        def plan_fake_cut():
            jump = args.jump
            if n_est:
                jump = min(jump, n_est - start - 15)
            if jump < 5:
                return None
            return (start, jump)

        if effect == "void":
            log("[1/3] Looking for scene cuts ...")
            thresholds = [args.scene]
            for t in (args.scene / 2.0, args.scene / 4.0):
                if t >= 0.03:
                    thresholds.append(round(t, 3))
            for t in thresholds:
                cuts = detect_scene_cuts(ffmpeg, args.input, fps, t)
                log("      sensitivity %.3f -> cuts at frames: %s" % (t, cuts if cuts else "none"))
                if cuts:
                    break
            if not cuts:
                fake = plan_fake_cut() if args.jump > 0 else None
                if not fake:
                    log("ERROR: no scene cuts found in this clip and the fake cut is off or does not fit.")
                    log("       Set 'Fake Cut Jump' above 0 (and keep the Start Frame well before the end).")
                    return 1
                cuts = [fake[0]]
                log("      NO real cuts: creating a FAKE CUT at frame %d (skipping %d frames)" % fake)
        elif effect == "classic":
            if args.jump > 0:
                fake = plan_fake_cut()
                if fake:
                    log("      FAKE CUT at frame %d (skipping %d frames) so there is something to melt" % fake)
                else:
                    log("      WARNING: Start Frame too close to the end for the fake cut; melt will be subtle.")
            else:
                log("      Fake cut is off: the melt is only visible if there is a real cut or strong motion.")
            cuts = [start]

        recover = recoveries(cuts, melt) if effect in ("void", "classic") else set()
        keys = set(cuts) | recover if effect in ("void", "classic") else (
            {start} if effect == "bloom" else set())

        # ---- 1) encode to MPEG-4 with I-frames exactly where we need them
        log("[1/3] Encoding to MPEG-4 (no B-frames) ...")
        vf = ["fps=%s" % fps]
        if fake:
            K, J = fake
            vf += ["select='lt(n,%d)+gte(n,%d)'" % (K, K + J), "setpts=N/(%s*TB)" % fps]
        vf += ["scale=trunc(iw/2)*2:trunc(ih/2)*2", "format=yuv420p"]
        enc = [ffmpeg, "-y", "-hide_banner", "-i", args.input, "-an", "-vf", ",".join(vf), "-r", fps,
               "-c:v", "mpeg4", "-q:v", str(args.quality), "-g", "100000", "-bf", "0"]
        if keys:
            log("      forced I-frames at frames:", sorted(keys),
                "(kept for recovery: %s)" % sorted(recover) if recover else "")
            enc += ["-force_key_frames", "expr:" + "+".join("eq(n,%d)" % k for k in sorted(keys))]
        raw = os.path.join(tmp, "src.m4v")
        run(enc + ["-f", "m4v", raw])

        # ---- 2) edit the bitstream
        log("[2/3] Editing the bitstream ...")
        with open(raw, "rb") as fh:
            frames = parse_m4v(fh.read())
        n_i = sum(1 for f in frames if f.ftype == I_FRAME)
        log("      frames: %d (I-frames: %d at %s)" % (
            len(frames), n_i, [i for i, f in enumerate(frames) if f.ftype == I_FRAME][:20]))
        if effect in ("classic", "bloom", "repeat") and start >= len(frames):
            log("ERROR: Start Frame %d is beyond the end of the video (%d frames)." % (start, len(frames)))
            return 1
        kw = dict(start=start, melt_length=melt, block_length=args.block_length,
                  count=args.count, recover=recover)
        fn = {"void": fx_void, "classic": fx_classic, "bloom": fx_bloom, "repeat": fx_repeat}[effect]
        moshed = fn(frames, **kw)
        log("      frames: %d -> %d (%+d)" % (len(frames), len(moshed), len(moshed) - len(frames)))
        if [f.data for f in moshed] == [f.data for f in frames]:
            log("ERROR: the effect changed nothing. " + {
                "void": "No I-frame to remove.",
                "classic": "No I-frame found near the chosen frame.",
                "bloom": "No I-frame found near Start Frame.",
                "repeat": "Nothing to repeat."}[effect])
            return 1
        mosh_path = os.path.join(tmp, "mosh.m4v")
        with open(mosh_path, "wb") as fh:
            for f in moshed:
                fh.write(f.data)

        # ---- audio follows the video edit
        audio_path = None
        if args.audio != "none":
            if not has_audio:
                log("      audio: the source has no audio track, result will be silent")
            else:
                log("      audio: following the video edit ...")
                try:
                    n_src = len(frames) + (fake[1] if fake else 0)
                    target = nearest_iframe(frames, start) if effect == "bloom" else None
                    plan = make_audio_plan(effect, dict(start=start, melt_length=melt,
                                           block_length=args.block_length, count=args.count),
                                           fake, cuts, target, n_src)
                    audio_path = process_audio(ffmpeg, tmp, args.input, plan, fpsv, args.audio,
                                               args.pitch, len(moshed))
                except Exception as exc:  # noqa
                    log("      WARNING: audio failed (%s). The result will be silent." % str(exc)[-300:])
                    audio_path = None

        # ---- 3) decode and re-encode to an editing-friendly file
        log("[3/3] Decoding the moshed stream and encoding the result (%s) ..." % args.codec)
        decode_and_mux(ffmpeg, mosh_path, fps, audio_path, args.output, args.codec)
        log("")
        log("DONE in %.1fs" % (time.time() - t0))
        log("OK:", args.output)
        return 0
    except Exception as exc:  # noqa
        log("ERROR:", exc)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
